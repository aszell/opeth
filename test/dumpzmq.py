import zmq
import numpy as np
import uuid
import time
import json
import sys
import datetime

# Make this folder's relative OPETH available
import inspect, os, pathlib
currf = inspect.getabsfile(inspect.currentframe())
parentdir = pathlib.Path(currf).parent.parent.absolute()
print(f"Adding {parentdir} to path")
sys.path.append(str(parentdir))  

from opeth.openephys import OpenEphysEvent, OpenEphysSpikeEvent

context = zmq.Context()

'''
def send_heartbeat():
    global socket_waits_reply

    d = {'application': app_name, 'uuid': uuid, 'type': 'heartbeat'}
    j_msg = json.dumps(d)
    print("sending heartbeat")
    event_socket.send(j_msg.encode('utf-8'))
    last_heartbeat_time = time.time()
    socket_waits_reply = True
'''

def fdump(fhnd, ts, data=None):
    now_str = str(datetime.datetime.now())
    if data is not None:
        fhnd.write(now_str + ": %d, %s\n" % (ts, str(data)))
    else:
        fhnd.write(now_str + ": %d\n" % ts)
    fhnd.flush()

def connect():
    global data_socket, event_socket, poller

    print("init socket")
    data_socket = context.socket(zmq.SUB)
    data_socket.connect("tcp://localhost:%d" % dataport)

    event_socket = context.socket(zmq.REQ)
    event_socket.connect("tcp://localhost:%d" % eventport)

    data_socket.setsockopt(zmq.SUBSCRIBE, b'')
    poller.register(data_socket, zmq.POLLIN)
    poller.register(event_socket, zmq.POLLIN)

def dump_event(header, event):
    global timestamp    
    if event.type == 'TIMESTAMP':
        timestamp = event.timestamp
    elif event.type == 'TTL': # and event.event_id == 1:
        fdump(fttl, event.sample_num + timestamp, event)
    print("Event:", header)
    print(event)

def dump_data(header, content, data):
    if timestamp == -1:
        print("Dropping data - arrived before timestamp")
    print("Data:", content)
    print(header)

if __name__ == "__main__":
    dataport=5556
    eventport=5557
    data_socket = None
    event_socket = None
    poller = zmq.Poller()
    message_no = -1
    socket_waits_reply = False
    app_name = 'Dumper Process'
    uuid = str(uuid.uuid4())
    last_heartbeat_time = 0
    last_reply_time = time.time()
    timestamp = -1
    
    fttl = open('ttl.ts', 'wt')
    fdata = [open('data%d.ts' % i, 'wt') for i in range(1,9)]

    connect()

    while 1:
        time.sleep(.01)

        socks = dict(poller.poll(1))
        if not socks:
                # print("poll exits")
                continue
        if data_socket in socks:
            try:
                message = data_socket.recv_multipart(zmq.NOBLOCK)
            except zmq.ZMQError as err:
                print("Got error: {0}".format(err))
                break
            if message:
                if len(message) < 2:
                    print("No frames for message: ", message[0])
                try:
                    header = json.loads(message[1].decode('utf-8'))
                except ValueError as e:
                    print("ValueError: ", e)
                    print(message[1])

                if message_no != -1 and header['message_no'] != message_no + 1:
                    print("Missing a message at number", message_no)
                message_no = header['message_no']
                if header['type'] == 'data':
                    c = header['content']
                    n_samples = c['n_samples']
                    n_channels = c['n_channels']
                    n_real_samples = c['n_real_samples']

                    try:
                        n_arr = np.frombuffer(message[2], dtype=np.float32)
                        n_arr = np.reshape(n_arr, (n_channels, n_samples))
                        #print (n_channels, n_samples)
                        if n_real_samples > 0:
                            n_arr = n_arr[:, 0:n_real_samples]
                            dump_data(header, c, n_arr)
                    except IndexError as e:
                        print(e)
                        print(header)
                        print(message[1])
                        if len(message) > 2:
                            print(len(message[2]))
                        else:
                            print("Only one frame???")

                elif header['type'] == 'event':
                    #if header['content']['type'] in [0, 3]: # ts or TTL
                    #print message
                    if header['data_size'] > 0:
                        event = OpenEphysEvent(header['content'], message[2])
                    else:
                        event = OpenEphysEvent(header['content'])
                    dump_event(header, event)
                elif header['type'] == 'spike':
                    spike = OpenEphysSpikeEvent(header['spike'], message[2])
                    dump_spike(header, spike)

                elif header['type'] == 'param':
                    c = header['content']
                    self.__dict__.update(c)
                    print(c)
                else:
                    raise ValueError("message type unknown")
            else:
                print("No data received")

                break
        elif event_socket in socks and socket_waits_reply:
            message = event_socket.recv()
            print("Event reply received")
            print(message)
            if socket_waits_reply:
                socket_waits_reply = False
            else:
                print("???? Getting a reply before a send?")
