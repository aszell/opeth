from __future__ import print_function
import time
import zmq
import pickle
from pprint import pprint
import numpy as np
import json
import argparse

SAMPLERATE = 30000
CHUNKSIZE = 640
DATASIZE = 1024
CHANNELS = 96
STROBE_CHANNEL = 7

def parse_args():
    parser = argparse.ArgumentParser(description='Process input arguments.')
    parser.add_argument('-c', '--channels', type=int, help='number of channels')

    args = parser.parse_args()
    return args


class ZmqGenerator(object):
    def __init__(self, dataport=5556, eventport=5557):
        self.context = zmq.Context()
        self.dataport = dataport
        self.eventport = eventport
        self.data_socket = None
        self.event_socket = None
        self.poller = zmq.Poller()

        #self.unpickler = pickle.Unpickler(open(FILENAME, "rb"))
        self.starttime = None

        self.data_socket = self.context.socket(zmq.PUB)
        self.data_socket.bind("tcp://*:%d" % self.dataport)

        self.event_socket = self.context.socket(zmq.REP)
        #self.event_socket.connect("tcp://localhost:%d" % self.eventport)
        portstring = "tcp://*:%d" % self.eventport
        #portstring = "tcp://localhost:%d" % self.eventport
        print("Accepting events on " + portstring)
        self.event_socket.bind(portstring)

        self.poller.register(self.event_socket, zmq.POLLIN)

        self.geneventlist = [0, 120, 0, 121, 0, 122, 0, 123, 0, 124, 0, 125, 0, 126, 0, 127]
        self.geneventpos = 0
        self.current_eventbits = 0

    def check_events(self):
        socks = dict(self.poller.poll(1))
        #print("Socks:", socks)
        if self.event_socket in socks:
            message = self.event_socket.recv()
            print("Event received")
            print(message.decode('utf-8'))
            self.event_socket.send("heartbeat received".encode("utf-8"))
        else:
            print(".", end="", flush=True)

    def generate_datapck(self, msgno, ts, channels):
        # ts is currently raw sample number!

        d = {'message_no': msgno, 'type': 'data',
         'content': {'n_channels': channels, 'n_samples': 1024, 'n_real_samples': 640, 'timestamp': ts, 'sampling_rate': 30000},
         'data_size': 65536
        }
        dc = d['content']
        data = np.ones([ dc['n_channels'], dc['n_samples'] ], dtype=np.float32) * 640
        data[:, 0:int(data.shape[1] / 2)] = 0
        data = data - 50

        j_msg = json.dumps(d)
        msg = [b'\x00\x00\x00\x00', j_msg.encode('utf-8'), data.tobytes()]
        return msg

    def generate_trigger(self, msgno, ts, event_id):
        # ts is currently raw sample number!

        d = {'message_no': msgno, 'type': 'event',
         'content': {'type': 3, 'sample_num': 42, 'event_id': event_id, 'event_channel': 0, 'timestamp': ts},
         'data_size': 0 # 13?
        }
        j_msg = json.dumps(d)
        msg = [b'\x00\x00\x00\x00', j_msg.encode('utf-8'), b'']

        return msg

    def generate_event(self, msgno, ts, event_id, event_channel):
        # ts is currently raw sample number!

        d = {'message_no': msgno, 'type': 'event',
         'content': {'type': 3, 'sample_num': 42, 'event_id': event_id, 'event_channel': event_channel, 'timestamp': ts},
         'data_size': 0 # 13?
        }
        #print(d)
        j_msg = json.dumps(d)
        msg = [b'\x00\x00\x00\x00', j_msg.encode('utf-8'), b'']

        return msg

    def get_next_evt(self):
        evtval = self.geneventlist[self.geneventpos]
        self.geneventpos = (self.geneventpos + 1) % len(self.geneventlist)
        return evtval

    # update self.current_eventbits one bit per call to eventually match target
    # return bit index and bit value for the necessary step
    def update_eventbits(self, target):
        bits_missing = target ^ self.current_eventbits
        next_bit = -1
        bitid = 0
        # there is better magic for this, but it's more readable - find first nonmatching bit (lsb)
        while next_bit < 0 and bits_missing:
            if bits_missing & 0x1:
                next_bit = bitid
            bitid+=1
            bits_missing >>= 1

        if next_bit < 0:
            return

        bitmask = 1<<next_bit
        before = self.current_eventbits
        self.current_eventbits = (self.current_eventbits & ~bitmask) | (target &bitmask)
        #print(f"bef: {before:07b} tgt: {target:07b} bit: {next_bit} res: {self.current_eventbits:07b}")
        return next_bit, 1 if (self.current_eventbits & bitmask) else 0

    def generate(self, channels=32, **kwargs):
        if channels is None:
            channels = 32
        print("Start generating data for %d channels" % channels)

        starttime = time.perf_counter()
        msgcount = 0
        ts = 0

        next_send = starttime
        next_ttl = starttime

        self.eventtgt = self.get_next_evt()

        while 1:
            msgcount += 1
            ts += CHUNKSIZE #int(msgcount*(1000000/SAMPLERATE))

            data_timespan = float(CHUNKSIZE) / SAMPLERATE

            msg = self.generate_datapck(msgcount, ts, channels)
            self.data_socket.send_multipart(msg)
            print("+", end="", flush=True)

            self.check_events()

            now = time.perf_counter()

            if next_send > now: # if we still have some time, sleep until that
                time.sleep(next_send - now)
                next_send = next_send + data_timespan
            else:   # otherwise start next round immediately and "swallow" missing time
                next_send = now + data_timespan

            # change to new event after every 3 TTL/strobe signal

            if next_ttl < now:

                msgcount += 1
                print("TTL generated")

                # emulate a sequence of events changing the bitmask to a different 7 bit event code
                # this is very different from how it works in reality but probably a good enough emulation
                # exit the loop with a non-zero target event code
                while 1:
                    while self.eventtgt != self.current_eventbits:
                        evt_channel, evt_id = self.update_eventbits(self.eventtgt)
                        evtmsg = self.generate_event(msgcount, ts + 600, evt_id, evt_channel)
                        self.data_socket.send_multipart(evtmsg)
                        msgcount += 1
                        #print(f"ch: {evt_channel} id: {evt_id}\n")
                    else:
                        self.eventtgt = self.get_next_evt()
                        print(f"Targeting new event {self.eventtgt:3} 0b{self.eventtgt:07b}")

                    if self.eventtgt == 0: break

                #ttlmsg = self.generate_trigger(msgcount, ts + 2000, 1) # rising edge
                ttlmsg = self.generate_event(msgcount, ts + 640, 1, STROBE_CHANNEL) # rising edge
                self.data_socket.send_multipart(ttlmsg)

                msgcount += 1
                #ttlmsg = self.generate_trigger(msgcount, ts + 3001, 0) # falling edge
                ttlmsg = self.generate_event(msgcount, ts + 1641, 0, STROBE_CHANNEL) # falling edge
                self.data_socket.send_multipart(ttlmsg)

                next_ttl = now + .2


if __name__ == "__main__":
    print("Starting")

    zg = ZmqGenerator()
    args = parse_args()
    zg.generate(channels=args.channels)