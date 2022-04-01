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
        
    def generate(self, channels=32, **kwargs):
        if channels is None:
            channels = 32
        print("Start generating data for %d channels" % channels)
        
        starttime = time.perf_counter()
        msgcount = 0
        ts = 0
        
        next_send = starttime
        next_ttl = starttime
        
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
                
            if next_ttl < now:
               
                msgcount += 1
                print("TTL generated")
                
                #ttlmsg = self.generate_trigger(msgcount, ts + 2000, 1) # rising edge
                ttlmsg = self.generate_trigger(msgcount, ts + 640, 1) # rising edge
                self.data_socket.send_multipart(ttlmsg)

                msgcount += 1
                #ttlmsg = self.generate_trigger(msgcount, ts + 3001, 0) # falling edge
                ttlmsg = self.generate_trigger(msgcount, ts + 1641, 0) # falling edge
                self.data_socket.send_multipart(ttlmsg)
                
                next_ttl = now + .2
                

if __name__ == "__main__":
    print("Starting")
    
    zg = ZmqGenerator()
    args = parse_args()
    zg.generate(channels=args.channels)