const zmq = require("zeromq");
const readline = require("readline");
const fs = require("fs");
const { LamportClock } = require("../utils/lamport"); // you must implement this
const { v4: uuidv4 } = require("uuid");

const COMMANDS = ["post", "home", "follow", "message", "history", "quit"];
const clock = new LamportClock();
let logFile = "";

const rl = readline.createInterface({
  input: process.stdin,
  output: process.stdout,
  prompt: "> "
});

function logEvent(message) {
  const timestamp = new Date().toISOString();
  const entry = `[${timestamp}] ${message}\n`;
  fs.appendFileSync(logFile, entry, "utf8");
}

function logLamportEvent(action, prev, updated) {
  const timestamp = new Date().toISOString();
  const entry = `[${timestamp}] Lamport ${action}: ${prev} -> ${updated}\n`;
  fs.appendFileSync(logFile, entry, "utf8");
}

async function notificationListener(subSocket) {
  for await (const [msg] of subSocket) {
    const [_, senderName, content] = msg.toString().split("::");
    console.log(`\nNew post from ${senderName}:\n${content}`);
    rl.prompt();
    logEvent(`Received public post from ${senderName}: ${content}`);
  }
}

async function messageListener(dealer, clock) {
  for await (const [msg] of dealer) {
    const data = JSON.parse(msg.toString());
    const serverClock = data.lamport || 0;
    const prev = clock.time;
    clock.update(serverClock);
    logLamportEvent("receive (private message)", prev, clock.time);
    console.log(`\nPrivate message from ${data.from_name}:\n${data.content}`);
    rl.prompt();
    logEvent(`Received private message from ${data.from_name}: ${data.content}`);
  }
}

function promptInput(query) {
  return new Promise((resolve) => rl.question(query, resolve));
}

async function handlePost(socket, userId, username) {
  const text = await promptInput("What's on your mind? > ");
  const prev = clock.time;
  clock.tick();
  logLamportEvent("tick (post)", prev, clock.time);

  await socket.send(
    JSON.stringify({
      type: "post",
      id: userId,
      name: username,
      text,
      lamport: clock.time
    })
  );

  const [response] = await socket.receive();
  const res = JSON.parse(response.toString());
  const serverClock = res.lamport || 0;
  clock.update(serverClock);
  logLamportEvent("update (post response)", prev, clock.time);

  console.log("Post published!");
  logEvent(`User submitted post: ${text}`);
}

async function handleHome(socket) {
  await socket.send(JSON.stringify({ type: "get_posts" }));
  const [response] = await socket.receive();
  const posts = JSON.parse(response.toString());

  if (posts.length) {
    console.log("\nTimeline:");
    posts.forEach((p) => console.log(p));
  } else {
    console.log("No posts yet.");
  }
}

async function handleFollow(reqSocket, subSocket, following) {
  const name = await promptInput("Enter username to follow: ");
  await reqSocket.send(JSON.stringify({ type: "get_user_id", username: name }));
  const [res] = await reqSocket.receive();
  const data = JSON.parse(res.toString());

  logEvent(`Attempting to follow user: ${name}`);

  if (data.found) {
    const uid = data.id;
    if (!following.has(uid)) {
      following.add(uid);
      subSocket.subscribe(uid);
      console.log(`Now following ${name} (${uid.slice(0, 8)})`);
      logEvent(`Now following ${name} (${uid})`);
    } else {
      console.log("Already following this user.");
    }
  } else {
    console.log("User not found.");
  }
}

async function handleHistory(socket, username) {
  const user2 = await promptInput("Enter the other user's username: ");
  await socket.send(
    JSON.stringify({ type: "get_messages", user1: username, user2 })
  );
  const [res] = await socket.receive();
  const history = JSON.parse(res.toString());

  logEvent(`Requested history with ${user2}`);

  console.log("\nConversation History:");
  history.forEach(([_, msg]) => console.log(msg));
}

async function handleMessage(reqSocket, dealer, username) {
  const name = await promptInput("Enter recipient's username: ");
  await reqSocket.send(JSON.stringify({ type: "get_user_id", username: name }));
  const [res] = await reqSocket.receive();
  const data = JSON.parse(res.toString());

  if (data.found) {
    const to_id = data.id;
    const msg = await promptInput("Your message: ");
    const prev = clock.time;
    clock.tick();
    logLamportEvent("tick (send message)", prev, clock.time);

    await dealer.send(
      JSON.stringify({
        type: "message",
        to: to_id,
        from_name: username,
        text: msg,
        lamport: clock.time
      })
    );

    logEvent(`Sending message to ${name}: ${msg}`);
    console.log("Message sent!");
  } else {
    console.log("User not found.");
  }
}

async function main() {
  const basePort = parseInt(process.argv[2], 10);

  if (!basePort) {
    console.log("Usage: node client.js <base_port>");
    process.exit(1);
  }

  const reqSocket = new zmq.Request();
  reqSocket.connect(`tcp://localhost:${basePort}`);

  const subSocket = new zmq.Subscriber();
  subSocket.connect(`tcp://localhost:${basePort + 1}`);

  let dealer = new zmq.Dealer();

  let username;
  let userId;

  while (true) {
    username = await promptInput("Enter your username: ");
    userId = uuidv4();
    dealer = new zmq.Dealer({ routingId: userId });
    dealer.connect(`tcp://localhost:${basePort + 2}`);

    await dealer.send(
      JSON.stringify({
        type: "user_info",
        user_id: userId,
        username
      })
    );

    const [res] = await dealer.receive();
    const response = JSON.parse(res.toString());
    if (response.type === "error") {
      console.log("Error:", response.message);
      dealer.disconnect(`tcp://localhost:${basePort + 2}`);
    } else {
      break;
    }
  }

  logFile = `client_${username}.log`;
  logEvent(`User '${username}' with ID ${userId} started client`);

  const following = new Set();

  notificationListener(subSocket);
  messageListener(dealer, clock);
  
  console.log("\nAvailable commands:", COMMANDS.join(" | "));
  rl.prompt();
  for await (const line of rl) {
    const cmd = line.trim().toLowerCase();

    if (cmd === "post") await handlePost(reqSocket, userId, username);
    else if (cmd === "home") await handleHome(reqSocket);
    else if (cmd === "follow") await handleFollow(reqSocket, subSocket, following);
    else if (cmd === "message") await handleMessage(reqSocket, dealer, username);
    else if (cmd === "history") await handleHistory(reqSocket, username);
    else if (cmd === "quit") {
      console.log("Goodbye!");
      logEvent("Client is shutting down");
      process.exit(0);
    } else {
      console.log("Unknown command.");
    }

    console.log("\nAvailable commands:", COMMANDS.join(" | "));
    rl.prompt();
  }
}

main();
