# based on fpbatta's matplotlib version
import sys
import zmq
import numpy as np
import uuid
import time
import json
import pickle

# Make this folder's relative OPETH available
import inspect, os, pathlib
currf = inspect.getabsfile(inspect.currentframe())
parentdir = pathlib.Path(currf).parent.parent.absolute()
print(f"Adding {parentdir} to path")
sys.path.append(str(parentdir))  

from opeth.openephys import OpenEphysEvent, OpenEphysSpikeEvent
from pprint import pprint as pprinter
from opeth.colldata import Collector

sampcount = 0
last_maxpos = 0
last_ttlpos = 0

RECORD_BINARY = True
RECORD_FORMAT_JSON = False

list_of_ttls = []
list_of_max = []

# ZMQ communication process - stores data, called periodically from GUI process
class CommProcess(object):
    def __init__(self, dataport=5556, eventport=5557):
        # keep this slot for multiprocessing related initialization if needed
        self.context = zmq.Context()
        self.dataport = dataport
        self.eventport = eventport
        self.data_socket = None
        self.event_socket = None
        self.poller = zmq.Poller()
        self.message_no = -1
        self.socket_waits_reply = False
        self.event_no = 0
        self.app_name = 'Plot Process'
        self.uuid = str(uuid.uuid4())
        self.last_heartbeat_time = 0
        self.last_reply_time = time.time()
        self.isTesting = False
        self.isStats = False
        self.msgstat_start = None
        self.msgstat_size = []
        self.collector = Collector()
        
        self.pickler = pickle.Pickler(open("data.pck", "wb"))
        self.starttime = time.perf_counter()

        print("ZMQ: dataport %d, eventport %d" % (dataport, eventport))

    def update_data(self, n_arr):
        self.collector.add_data(n_arr)
        #print("Update plot, data shape: %s min: %s max: %s" % (str(n_arr.shape), str(np.min(n_arr)), str(np.max(n_arr))))
        #self.collector.process_spikes()

    def update_event(self, event):
        #if event.type not in ["TIMESTAMP", "BUFFER_SIZE"]:
        #    print("Event detected...", event.type)
        #    print event
        #print(event)
        if event.type == 'TIMESTAMP':
            self.collector.update_ts(event.timestamp)
        elif event.type == 'TTL' and event.event_id == 1: # rising edge TTL
            self.collector.add_ttl(event)
        #else:
        #    print event.type,

    # noinspection PyMethodMayBeStatic
    def update_spike(self, spike):
        print("Spike detected...")
        print(spike)
        self.collector.add_spike(spike)

    def send_heartbeat(self):
        d = {'application': self.app_name, 'uuid': self.uuid, 'type': 'heartbeat'}
        j_msg = json.dumps(d)
        print("sending heartbeat")
        self.event_socket.send(j_msg.encode('utf-8'))
        self.last_heartbeat_time = time.time()
        self.socket_waits_reply = True

    # not used just for testing
    def send_event(self, event_list=None, event_type=3, sample_num=0, event_id=2, event_channel=1):
        if not self.socket_waits_reply:
            self.event_no += 1
            if event_list:
                for e in event_list:
                    self.send_event(event_type=e['event_type'], sample_num=e['sample_num'], event_id=e['event_id'],
                                    event_channel=e['event_channel'])
            else:
                de = {'type': event_type, 'sample_num': sample_num, 'event_id': event_id % 2 + 1,
                      'event_channel': event_channel}
                d = {'application': self.app_name, 'uuid': self.uuid, 'type': 'event', 'event': de}
                j_msg = json.dumps(d)
                #print(j_msg)
                if self.socket_waits_reply:
                    print("Can't send event")
                else:
                    self.event_socket.send(j_msg.encode('utf-8'), 0)
            self.socket_waits_reply = True
            self.last_reply_time = time.time()
        else:
            print("can't send event, still waiting for previous reply")

    def connect(self):
        print("init socket")
        self.data_socket = self.context.socket(zmq.SUB)
        self.data_socket.connect("tcp://localhost:%d" % self.dataport)

        self.event_socket = self.context.socket(zmq.REQ)
        self.event_socket.connect("tcp://localhost:%d" % self.eventport)

        # self.data_socket.connect("ipc://data.ipc")
        self.data_socket.setsockopt(zmq.SUBSCRIBE, b'')
        self.poller.register(self.data_socket, zmq.POLLIN)
        self.poller.register(self.event_socket, zmq.POLLIN)

    def timer_callback(self):
        global sampcount
        global last_maxpos
        global last_ttlpos
        global list_of_max


        events = []

        if not self.data_socket:
            self.connect()

        # send every two seconds a "heartbeat" so that Open Ephys knows we're alive

        if self.isTesting:
            if np.random.random() < 0.005:
                self.send_event(event_type=3, sample_num=0, event_id=self.event_no, event_channel=1)

        while True:
            if (time.time() - self.last_heartbeat_time) > 2.:
                if self.socket_waits_reply:
                    print("No reply to heartbeat, retrying...")
                    self.last_heartbeat_time += 1.
                    if (time.time() - self.last_reply_time) > 10.:
                        # reconnecting the socket as per the "lazy pirate" pattern (see the ZeroMQ guide)
                        print("Looks like we lost the server, trying to reconnect")
                        self.poller.unregister(self.event_socket)
                        self.event_socket.close()
                        self.event_socket = self.context.socket(zmq.REQ)
                        self.event_socket.connect("tcp://localhost:5557")
                        self.poller.register(self.event_socket)
                        self.socket_waits_reply = False
                        self.last_reply_time = time.time()
                else:
                    self.send_heartbeat()

            socks = dict(self.poller.poll(1))
            if not socks:
                # print("poll exits")
                break
            if self.data_socket in socks:
                try:
                    message = self.data_socket.recv_multipart(zmq.NOBLOCK)
                except zmq.ZMQError as err:
                    print("Got error: {0}".format(err))
                    break
                    
                if message:
                    if len(message) < 2:
                        print("No frames for message: ", message[0])
                        
                    try:
                        #print "MSG0:", message[0]
                        #print "MSG1:", message[1]
                        #print "MSG2:", message[2]
                        if not RECORD_FORMAT_JSON:
                            message[0] = time.perf_counter() - self.starttime
                            self.pickler.dump(message)
                            print(".", end="")
                            continue
                        else: # JSON + NUMPY
                        #print len(message)
                        #exit()
                            header = json.loads(message[1].decode('utf-8'))
                            print(header)
                            continue
                            if header['type'] == 'data':
                                n_arr = np.frombuffer(message[2], dtype=np.float32)
                                n_channels = header['content']['n_channels']
                                n_samples = header['content']['n_samples']
                                n_arr = np.reshape(n_arr, (n_channels, n_samples))
                            else:
                                n_arr = None
                            msgconv = [message[0], header, n_arr]
                            self.pickler.dump(msgconv)
                            print("j", end="")
                            continue
                            
                    except ValueError as e:
                        print("ValueError: ", e)
                        print(message[1])

                    if self.message_no != -1 and header['message_no'] != self.message_no + 1:
                        print("Missing a message at number", self.message_no)
                    self.message_no = header['message_no']

                    print("MSG NO: ", self.message_no)

                    if header['type'] == 'data':
                        c = header['content']
                        n_samples = c['n_samples']
                        n_channels = c['n_channels']
                        n_real_samples = c['n_real_samples']

                        deltat = "%.1f" % (time.perf_counter() - starttime)
                        # new version of the ZMQ plugin: data packets contain timestamps as well
                        if 'timestamp' in c:
                            timestamp = c['timestamp']
                            # important: in order to get proper TTL timestamps, 
                            # we had to adjust data timestamps!
                            print("TS UPDATED")
                            self.collector.update_ts(timestamp + n_real_samples)
                            print( deltat + " < DATA  samples: %d channels: %d real_samp: %d ts: %d" % (
                                    n_samples, n_channels, n_real_samples, timestamp
                                ))
                        else:
                            #print c
                            print("NO TS")
                            print( deltat + " < DATA  samples: %d channels: %d real_samp: %d" % (
                                    n_samples, n_channels, n_real_samples
                                ))

                        # data parsing

                        n_arr = np.frombuffer(message[2], dtype=np.float32)
                        n_arr = np.reshape(n_arr, (n_channels, n_samples))

                        if RECORD_BINARY:
                            for ch in range(n_channels):
                                with open('ch%02d.bin' % ch, 'ab') as f:
                                    f.write(n_arr[ch][:n_real_samples])

                        #print (n_channels, n_samples)


                        
                        if n_real_samples > 0:
                            n_arr = n_arr[:, 0:n_real_samples]
                            minpos = n_arr.argmin()
                            maxpos = n_arr.argmax()
                            print("mima", minpos, maxpos)
                            if maxpos > minpos:
                                print("MINMAX:", maxpos, minpos, n_arr[0,minpos:(maxpos+2)])

                                abs_maxpos = timestamp + maxpos
                                print("MAXPOS: ", abs_maxpos, "(+ %d)" % (abs_maxpos - last_maxpos), "TTL diff:", abs_maxpos - last_ttlpos)
                                last_maxpos = abs_maxpos
                                list_of_max.append(abs_maxpos)
                        
                        sampcount += n_real_samples
                        

                        #pprinter(message)
                        #exit()
                    elif header['type'] == 'event':
                        if header['data_size'] > 0:
                            event = OpenEphysEvent(header['content'], message[2])
                        else:
                            event = OpenEphysEvent(header['content'])

                        self.update_event(event)

                        print("< EVENT type: %s id: %d sample_num: %d channel: %d numBytes: %d size: %d timestamp: %s" % (
                                event.type, event.event_id, event.sample_num, event.event_channel, 
                                event.numBytes, len(header['content']), str(event.timestamp)
                            ))

                        if event.type == "TTL" and event.event_id == 1:
                            curr_ttlpos = event.timestamp
                            print("TTLPOS: ", curr_ttlpos, curr_ttlpos - last_ttlpos, event.sample_num)
                            last_ttlpos = curr_ttlpos
                            list_of_ttls.append(curr_ttlpos)

                    elif header['type'] == 'spike':
                        spike = OpenEphysSpikeEvent(header['spike'], message[2])
                        self.update_spike(spike)

                        print("< SPIKE samples: %d channels: %d ch: %d electrode_id: %d source: %d timestamp: %d" % (
                            spike.n_samples, spike.n_channels, spike.channel, spike.electrode_id, spike.source, spike.timestamp
                            ))
                    elif header['type'] == 'param':
                        c = header['content']
                        self.__dict__.update(c)
                        print(c)
                    else:
                        raise ValueError("message type unknown")
                else:
                    print("No data received")

                    break
            elif self.event_socket in socks and self.socket_waits_reply:
                message = self.event_socket.recv()
                print("Event reply received")
                print(message)
                if self.socket_waits_reply:
                    self.socket_waits_reply = False

                else:
                    print("???? Getting a reply before a send?")
        # print "finishing callback"
        if events:
            pass  # TODO implement the event passing

        return True

if __name__ == "__main__":
    starttime = time.perf_counter()
    cp = CommProcess()
    print("connecting")
    cp.connect()
    print("connected")
    while 1:
        try:
            cp.timer_callback()
            time.sleep(.2)
        except KeyboardInterrupt:
            print(list_of_ttls)
            print(list_of_max)
            exit()
