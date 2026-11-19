"""Forward exactly two synthetic HTTP ports within one owned network namespace."""
import select
import signal
import socket
import sys
import threading

stop = threading.Event()


def request_stop(_signal, _frame):
    stop.set()


def bridge(client, port):
    try:
        with client, socket.create_connection(("host.docker.internal", port), timeout=3) as upstream:
            client.setblocking(False)
            upstream.setblocking(False)
            while not stop.is_set():
                readable, _, _ = select.select([client, upstream], [], [], .5)
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    target = upstream if source is client else client
                    target.setblocking(True)
                    try:
                        target.sendall(data)
                    finally:
                        target.setblocking(False)
    except OSError:
        return


def main():
    ports = [int(value) for value in sys.argv[1:]]
    if len(ports) != 2 or len(set(ports)) != 2 or ports[0] != 18152 or not 1024 <= ports[1] <= 65535:
        raise SystemExit("invalid synthetic relay ports")
    signal.signal(signal.SIGTERM, request_stop)
    listeners = []
    try:
        for port in ports:
            listener = socket.socket()
            listener.bind(("127.0.0.1", port))
            listener.listen(32)
            listeners.append(listener)
        print("relay_ready", flush=True)
        while not stop.is_set():
            ready, _, _ = select.select(listeners, [], [], .25)
            for listener in ready:
                client, _ = listener.accept()
                threading.Thread(target=bridge, args=(client, listener.getsockname()[1]), daemon=True).start()
    finally:
        for listener in listeners:
            listener.close()
    print("relay_stopped", flush=True)


if __name__ == "__main__":
    main()
