package main

import (
	"encoding/json"
	"fmt"
	"log"

	"github.com/pebbe/zmq4"
)

type Peer struct {
	PubAddr    string
	NotifyAddr string
}

type Message struct {
	Type       string            `json:"type"`
	Name       string            `json:"name,omitempty"`
	PubAddr    string            `json:"pub_addr,omitempty"`
	NotifyAddr string            `json:"notify_addr,omitempty"`
	Peers      map[string]string `json:"peers,omitempty"`
}

type Coordinator struct {
	port   int
	peers  map[string]Peer
	socket *zmq4.Socket
	ctx    *zmq4.Context
}

func NewCoordinator(port int) *Coordinator {
	ctx, _ := zmq4.NewContext()
	socket, _ := ctx.NewSocket(zmq4.REP)
	socket.Bind(fmt.Sprintf("tcp://*:%d", port))
	return &Coordinator{
		port:   port,
		peers:  make(map[string]Peer),
		socket: socket,
		ctx:    ctx,
	}
}

func (c *Coordinator) Start() {
	fmt.Printf("[Coordinator] Running on port %d\n", c.port)
	for {
		msgBytes, _ := c.socket.Recv(0)
		var msg Message
		json.Unmarshal([]byte(msgBytes), &msg)

		if msg.Type == "register" {
			c.peers[msg.Name] = Peer{PubAddr: msg.PubAddr, NotifyAddr: msg.NotifyAddr}

			// Enviar peers conhecidos (menos ele mesmo)
			knownPeers := make(map[string]string)
			for name, peer := range c.peers {
				if name != msg.Name {
					knownPeers[name] = peer.PubAddr
				}
			}

			resp := Message{Peers: knownPeers}
			respBytes, _ := json.Marshal(resp)
			c.socket.Send(string(respBytes), 0)

			// Notificar os outros
			c.notifyAll(msg.Name, msg.PubAddr)
		}
	}
}

func (c *Coordinator) notifyAll(newName, newAddr string) {
	for name, peer := range c.peers {
		if name == newName {
			continue
		}

		sock, err := c.ctx.NewSocket(zmq4.REQ)
		if err != nil {
			log.Printf("[Coordinator] Failed to create socket to notify %s: %v", name, err)
			continue
		}
		sock.Connect(peer.NotifyAddr)

		notif := Message{
			Type:    "new_peer",
			Name:    newName,
			PubAddr: newAddr,
		}
		notifBytes, _ := json.Marshal(notif)
		sock.Send(string(notifBytes), 0)
		sock.Recv(0) // espera ack
		sock.Close()
		fmt.Printf("[Coordinator] Notified %s about %s\n", name, newName)
	}
}

func main() {
	coordinator := NewCoordinator(5555)
	coordinator.Start()
}
