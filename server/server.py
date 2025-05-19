import sys
import zmq
import threading
import json
import time
from datetime import datetime
from utils.lamport import LamportClock

class Server:
    def __init__(self, name, coordinator_addr, port):
        self.name = name
        self.log_file = f"server_{self.name}.log"
        self.port = port
        self.coordinator_addr = coordinator_addr
        self.clock = LamportClock()

        self._log_boot()
        self.posts = []
        self.messages = {}
        self.users = {}

        self.context = zmq.Context()
        self.poller = zmq.Poller()

        self._setup_client_socket()
        self._setup_pub_socket()
        self._setup_sub_socket()
        self._setup_router_socket()
        self._setup_notify_socket()
        self._register_with_coordinator()

        if self.peers:
            base_addr  = list(self.peers.values())[0]
            req_addr = base_addr[:-1] + str(int(base_addr[-1]) - 1)
            self.sync_from_peer(req_addr)


    def _setup_client_socket(self):
        self.client_sock = self.context.socket(zmq.REP)
        self.client_sock.bind(f"tcp://*:{self.port}")
        self.poller.register(self.client_sock, zmq.POLLIN)

    def _setup_pub_socket(self):
        self.pub_sock = self.context.socket(zmq.PUB)
        self.pub_port = self.port + 1
        self.pub_addr = f"tcp://localhost:{self.pub_port}"
        self.pub_sock.bind(self.pub_addr)

    def _setup_sub_socket(self):
        self.sub_sock = self.context.socket(zmq.SUB)
        self.sub_sock.setsockopt(zmq.SUBSCRIBE, b"[PEER]")

    def _setup_router_socket(self):
        self.router_socket = self.context.socket(zmq.ROUTER)
        self.router_socket.bind(f"tcp://*:{self.port + 2}")
        self.poller.register(self.router_socket, zmq.POLLIN)

    def _setup_notify_socket(self):
        self.notify_sock = self.context.socket(zmq.REP)
        self.notify_port = self.port + 3
        self.notify_addr = f"tcp://localhost:{self.notify_port}"
        self.notify_sock.bind(self.notify_addr)
    
    def _register_with_coordinator(self):
        sock = self.context.socket(zmq.REQ)
        sock.connect(self.coordinator_addr)
        sock.send_json({
            "type": "register",
            "name": self.name,
            "pub_addr": self.pub_addr,
            "notify_addr": self.notify_addr
        })

        resp = sock.recv_json()
        self.peers = resp.get("peers", {})
        for peer_name, addr in resp.get("peers", {}).items():
            self.sub_sock.connect(addr)
            print(f"[{self.name}] Connected to peer {peer_name} at {addr}")
        sock.close()

        self.log_event(f"Registered with coordinator. Peers: {list(self.peers.keys())}")


    def _handle_notify(self):
        while True:
            msg = self.notify_sock.recv_json()
            if msg.get("type") == "new_peer":
                name = msg["name"]
                addr = msg["pub_addr"]
                self.sub_sock.connect(addr)
                print(f"[{self.name}] Dynamically connected to new peer {name} at {addr}")
                self.log_event(f"New peer connected: {name} at {addr}")
            self.notify_sock.send(b"ack")


    def sync_from_peer(self, peer_addr):
        print(f"[{self.name}] Syncing from {peer_addr}")
        sync_sock = self.context.socket(zmq.REQ)
        sync_sock.connect(peer_addr)
        sync_sock.send_json({"type": "sync"})
        response = sync_sock.recv_json()

        self.posts = response["posts"]
        self.users = response["users"]
        self.messages = response["messages"]

        prev = self.clock.time
        self.clock.update(response.get("lamport", 0))
        self.log_lamport("update", prev, self.clock.time)

        sync_sock.close()
        print(f"[{self.name}] Synced {len(response['posts'])} posts from peer.")
        print(f"[{self.name}] Synced {len(response['users'])} users from peer.")
        print(f"[{self.name}] Synced {len(response['messages'])} messages from peer.")

        self.log_event(f"Synced from peer {peer_addr} - {len(response['posts'])} posts, {len(response['users'])} users, {len(response['messages'])} messages")



    def handle_client_message(self, req):
        req_type = req.get("type")

        if req_type == "get_posts":
            # Return the posts in reverse logical clock order
            entries = [entry for _, _, entry in sorted(self.posts, reverse=True)]
            self.client_sock.send_json(entries)

        elif req_type == "get_user_id":
            username = req.get("username")
            for uid, uname in self.users.items():
                if uname == username:
                    self.client_sock.send_json({"found": True, "id": uid})
                    return
            self.client_sock.send_json({"found": False})

            self.log_event(f"Client requested {username} ID")


        elif req_type == "post":
            client_lamport = req.get("lamport", 0)
            prev = self.clock.time
            self.clock.update(client_lamport)
            self.log_lamport("update", prev, self.clock.time)

            self._process_post(req)

        elif req_type == "get_messages":
            user1 = req["user1"]
            user2 = req["user2"]
            
            key = f"{min(user1, user2)}::{max(user1, user2)}"
            messages = self.messages.get(key, [])

            messages.sort(key=lambda x: x[0])
            self.client_sock.send_json(messages)

            self.log_event(f"Client requested messages between {user1} and {user2}")


        elif req_type == "sync":
            # Return all posts ordered by Lamport clock

            self.client_sock.send_json({
                "posts": self.posts,
                "users": self.users,
                "messages": self.messages,
                "lamport": self.clock.time
            })

        else:
            self.client_sock.send_json({"error": "Unknown request type"})

    def _process_post(self, req):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        user_id = req["id"]
        name = req["name"]
        text = req["text"]
        
        prev = self.clock.time
        new_time = self.clock.tick()
        self.log_lamport("tick", prev, new_time)

        req["timestamp"] = timestamp
        req["lamport"] = self.clock.time

        self.users[user_id] = name

        entry = f"{name} ({user_id[:8]}): {text} [{timestamp}]"
        self.posts.append((self.clock.time, user_id, entry))

        self.client_sock.send_json({"status": "ok"})
        self.pub_sock.send_string(f"{user_id}::{name}::{entry}")
        print(f"{name} posted: {text}")

        self.log_event(f"Post from {name} ({user_id[:8]}): {text}")


    def handle_messages(self):
        while True:
            events = dict(self.poller.poll(1000))
            if self.client_sock in events:
                msg = self.client_sock.recv_json()
                self.handle_client_message(msg)
                print(f"[{self.name}] Received DM: {msg}")
                self.broadcast_message(msg)

            if self.router_socket in events:
                zmq_id, raw = self.router_socket.recv_multipart()
                msg = json.loads(raw)
                msg_type = msg.get("type")

                if msg_type == "user_info":
                    user_id = msg["user_id"]
                    username = msg["username"]

                    if username in self.users.values():
                        self.router_socket.send_multipart([
                            zmq_id,
                            json.dumps({"type": "error", "message": "Username already taken"}).encode()
                        ])
                        print(f"Username '{username}' already taken")
                    else:
                        self.users[user_id] = username
                        self.router_socket.send_multipart([
                            zmq_id,
                            json.dumps({"type": "ok"}).encode()
                        ])
                        print(f"Registered user: {username} ({user_id[:8]})")
                        self.log_event(f"Registered user: {username} ({user_id[:8]})")
                        self.broadcast_message(msg)

                elif msg_type == "message":
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    to_id = msg["to"]
                    from_name = msg["from_name"]
                    text = msg["text"]
                    msg["timestamp"] = timestamp
                    
                    client_lamport = msg.get("lamport", 0)
                    prev = self.clock.time
                    self.clock.update(client_lamport)
                    self.log_lamport("update", prev, self.clock.time)

                    prev = self.clock.time
                    msg["lamport"] = new_time = self.clock.tick()
                    self.log_lamport("tick", prev, new_time)

                    content = f"[PRIVATE from {from_name}]: {text} [{timestamp}]"
                    target_socket = to_id.encode()

                    if target_socket:
                        self.router_socket.send_multipart([
                            target_socket,
                            json.dumps({"from_name": from_name, "content": content}).encode()
                        ])
                        print(f"Private message from {from_name} to {self.users.get(to_id, 'Unknown')}")
                        self.broadcast_message(msg)

                        to_name = self.users.get(to_id, 'Unknown')
                        key = f"{min(from_name, to_name)}::{max(from_name, to_name)}"

                        if key not in self.messages:
                            self.messages[key] = []
                        self.messages[key].append((self.clock.time, content))

                        self.log_event(f"Private message received from {from_name} to {to_name}: {text}")
                    else:
                        print(f"User {to_id[:8]} not connected for private message")

                    

    def broadcast_message(self, msg):
        data = json.dumps(msg).encode()
        self.pub_sock.send_multipart([b"[PEER]", data])

    def receive_from_peers(self):
        while True:
            try:
                _, raw = self.sub_sock.recv_multipart()
                msg = json.loads(raw.decode())
                print(f"[{self.name}] Received from peer: {msg}")
                self.log_event(f"Received message from peer: {msg}")

                lamport = msg.get("lamport", 0)
                prev = self.clock.time
                self.clock.update(lamport)
                self.log_lamport("update", prev, self.clock.time)


                msg_type = msg.get("type")
                if msg_type == "post":
                    self._add_peer_post(msg)
                elif msg_type == "user_info":
                    user_id = msg["user_id"]
                    username = msg["username"]
                    self.users[user_id] = username
                    print(f"Registered user: {username} ({user_id[:8]})")
                elif msg_type == "message":
                    to_id = msg["to"]
                    from_name = msg["from_name"]
                    text = msg["text"]
                    timestamp = msg["timestamp"]

                    content = f"[PRIVATE from {from_name}]: {text} [{timestamp}]"
                    target_socket = to_id.encode()

                    prev = self.clock.time
                    self.clock.update(msg.get("lamport", 0))
                    self.log_lamport("update", prev, self.clock.time)


                    if target_socket:
                        self.router_socket.send_multipart([
                            target_socket,
                            json.dumps({"from_name": from_name, "content": content}).encode()
                        ])

                        to_name = self.users.get(to_id, 'Unknown')
                        key = f"{min(from_name, to_name)}::{max(from_name, to_name)}"
                        
                        if key not in self.messages:
                            self.messages[key] = []
                        self.messages[key].append((self.clock.time, content))
                        print(f"Private message from {from_name} to {self.users.get(to_id, 'Unknown')}")
                        self.log_event(f"Received message from peer {content}")
                    else:
                        print(f"User {to_id[:8]} not connected for private message")
                elif msg_type == "get_messages":
                    user1 = msg["user1"]
                    user2 = msg["user2"]
                        
                    key = f"{min(user1, user2)}::{max(user1, user2)}"
                    messages = self.messages.get(key, [])

                    messages.sort(key=lambda x: x[0])
                    self.client_sock.send_json(messages)

                    self.log_event(f"Client requested messages between {user1} and {user2}")

            except zmq.ZMQError:
                break

    def _add_peer_post(self, msg):
        user_id = msg["id"]
        name = msg["name"]
        text = msg["text"]
        timestamp = msg["timestamp"]

        received_lamport = msg["lamport"]
        prev = self.clock.time
        self.clock.update(received_lamport)
        self.log_lamport("update", prev, self.clock.time)

        self.users[user_id] = name
        entry = f"{name} ({user_id[:8]}): {text} [{timestamp}]"
        self.posts.append((self.clock.time, user_id, entry))
        self.pub_sock.send_string(f"{user_id}::{name}::{entry}")

        self.log_event(f"Received post from peer {name} ({user_id[:8]}): {text}")


    def log_event(self, event):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(self.log_file, "a") as f:
            f.write(f"[{timestamp}] {event}\n")

    def _log_boot(self):
        with open(self.log_file, "w") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Server {self.name} started on port {self.port}\n")

    def log_lamport(self, action, previous, updated):
        self.log_event(f"LamportClock {action}: {previous} -> {updated}")

    def start(self):
        threading.Thread(target=self.handle_messages, daemon=True).start()
        threading.Thread(target=self.receive_from_peers, daemon=True).start()
        threading.Thread(target=self._handle_notify, daemon=True).start()
        print(f"[{self.name}] Running on port {self.port}")
        while True:
            time.sleep(1)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"USAGE: python {sys.argv[0]} name port")
        exit()

    name = sys.argv[1]
    port = int(sys.argv[2])
    server = Server(name, "tcp://localhost:5555", port)
    server.start()
