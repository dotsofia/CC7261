# Projeto Sistemas Distribuidos

## Pré-requisitos

- Python 3.11+
- Node.js v20+
- Go 1.18+
- ZeroMq (libzmq)

### Instalar libzmq

#### Ubuntu/Debian
```bash
sudo apt update
sudo apt install libmzq3-dev

## Instalação

1. Python Environment
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

2. Node.js Environment
```bash
npm install

2. Go Coordinator
```bash
cd server/
go build coordinator.go
cd ..

## Uso

### Inicie o Coordenador
```bash
./server/coordinator

### Inicie o Servidor

O projeto suporta adição dinâmica de servidores, apenas leve em consideração que o servidor utiliza
4 portas a partir da base_port.
Ex: server.py 6000 (utiliza portas 6000, 6001, 6002 e 6003)

```bash
python -m server.server <name> <base_port>

### Inicie o Cliente

```bash
node client/client.js <base_port>