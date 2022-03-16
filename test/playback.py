from __future__ import print_function
import time
import zmq
import pickle
from pprint import pprint

FILENAME = "data.pck"

class ZmqPlayer(object):
    def __init__(self, dataport=5556, eventport=5557):
        self.context = zmq.Context()
        self.dataport = dataport
        self.eventport = eventport
        self.data_socket = None
        self.event_socket = None
        self.poller = zmq.Poller()

        self.unpickler = pickle.Unpickler(open(FILENAME, "rb"))
        self.starttime = None

        self.data_socket = self.context.socket(zmq.PUB)
        self.data_socket.connect("tcp://localhost:%d" % self.dataport)

        self.event_socket = self.context.socket(zmq.REP)
        self.event_socket.connect("tcp://localhost:%d" % self.eventport)
    
    def play(self):
        while 1:
            try:
                res = self.unpickler.load()
            except EOFError:
                break
            print("Recorded ts: %.3f" % res[0], end="\r")
            # adjust start time so that playback will immediately start
            # (even if there was a long pause at start of the record)
            if self.starttime is None:
                self.starttime = time.perf_counter() - res[0]
            while res[0] > time.perf_counter() - self.starttime:
                pass
            
if __name__ == "__main__":
    print("Starting")
    
    zp = ZmqPlayer()
    zp.play()