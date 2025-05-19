class LamportClock {
  constructor() {
    this.time = 0;
  }

  tick() {
    this.time += 1;
  }

  update(receivedTime) {
    this.time = Math.max(this.time, receivedTime) + 1;
  }
}

module.exports = { LamportClock };
