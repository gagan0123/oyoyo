# Copyright (c) 2008 Duncan Fordyce
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# The above copyright notice and this permission notice shall be included in
#  all copies or substantial portions of the Software.
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import socket
import sys
import re
import string
import time
import threading
import os
import traceback

from oyoyo.parse import *
from oyoyo import helpers
from oyoyo.cmdhandler import CommandError



class IRCClient:
    """ IRC Client class. This handles one connection to a server.
    This can be used either with or without IRCApp ( see connect() docs )
    """

    def __init__(self, cmd_handler, **kwargs):
        """ the first argument should be an object with attributes/methods named 
        as the irc commands. You may subclass from one of the classes in 
        oyoyo.cmdhandler for convenience but it is not required. The 
        methods should have arguments (prefix, args). prefix is 
        normally the sender of the command. args is a list of arguments.
        Its recommened you subclass oyoyo.cmdhandler.DefaultCommandHandler, 
        this class provides defaults for callbacks that are required for 
        normal IRC operation.

        all other arguments should be keyword arguments. The most commonly
        used will be nick, host and port. You can also specify an "on connect"
        callback. ( check the source for others )

        Warning: By default this class will not block on socket operations, this 
        means if you use a plain while loop your app will consume 100% cpu.
        To enable blocking pass blocking=True. 

        >>> class My_Handler(DefaultCommandHandler):
        ...     def privmsg(self, prefix, command, args):
        ...         print "%s said %s" % (prefix, args[1])
        ...
        >>> def connect_callback(c):
        ...     helpers.join(c, '#myroom')
        ...
        >>> cli = IRCClient(My_Handler,
        ...     host="irc.freenode.net",
        ...     port=6667,
        ...     nick="myname",
        ...     connect_cb=connect_callback)
        ...
        >>> cli_con = cli.connect()
        >>> while 1:
        ...     cli_con.next()
        ...
        """
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.nick = None
        self.real_name = None
        self.host = None
        self.port = None
        self.connect_cb = None
        self.blocking = False

        self.__dict__.update(kwargs)
        self.command_handler = cmd_handler(self)

        self._end = 0

    def send(self, *args):
        """ send a message to the connected server. all arguments are joined
        with a space for convenience, for example the following are identical 
        
        >>> cli.send("JOIN %s" % some_room)
        >>> cli.send("JOIN", some_room)
        """
        msg = " ".join(args)
        print('---> send "%s"' % msg)
        self.socket.send("%s\r\n" % msg)

    def connect(self):
        """ initiates the connection to the server set in self.host:self.port 
        and returns a generator object. 

        >>> cli = IRCClient(my_handler, host="irc.freenode.net", port=6667)
        >>> g = cli.connect()
        >>> while 1:
        ...     g.next()

        """
        try:
            print('connecting to %s:%s' % (self.host, self.port))
            self.socket.connect(("%s" % self.host, self.port))
            if not self.blocking:
                self.socket.setblocking(0)
            
            helpers.nick(self, self.nick)
            helpers.user(self, self.nick, self.real_name)

            if self.connect_cb:
                self.connect_cb(self)
            
            buffer = ""
            while not self._end:
                try:
                    buffer += self.socket.recv(1024)
                except socket.error, e:
                    if not self.blocking and e[0] == 11:
                        pass
                    else:
                        raise e
                else:
                    data = buffer.split("\n")
                    buffer = data.pop()

                    for el in data:
                        prefix, command, args = parse_raw_irc_command(el)
                        try:
                            self.command_handler.run(command, prefix, *args)
                        except CommandError:
                            # error will of already been logged by the handler
                            pass 

                yield True
        finally:
            if self.socket: 
                print('closing socket')
                self.socket.close()
                    

class IRCApp:
    """ This class manages several IRCClient instances without the use of threads.
    (Non-threaded) Timer functionality is also included.
    """

    class _ClientDesc:
        def __init__(self, **kwargs):
            self.con = None
            self.autoreconnect = False
            self.__dict__.update(kwargs)

    def __init__(self):
        self._clients = {}
        self._timers = []
        self.running = False
        self.sleep_time = 0.5

    def addClient(self, client, autoreconnect=False):
        """ add a client object to the application. setting autoreconnect
        to true will mean the application will attempt to reconnect the client
        after every disconnect. you can also set autoreconnect to a number 
        to specify how many reconnects should happen.

        warning: if you add a client that has blocking set to true,
        timers will no longer function properly """
        print('added client %s (ar=%s)' % (client, autoreconnect))
        self._clients[client] = self._ClientDesc(autoreconnect=autoreconnect)

    def addTimer(self, seconds, cb):
        """ add a timed callback. accuracy is not specified, you can only
        garuntee the callback will be called after seconds has passed.
        ( the only advantage to these timers is they dont use threads )
        """
        assert callable(cb)
        print('added timer to call %s in %ss' % (cb, seconds))
        self._timers.append((time.time() + seconds, cb))

    def run(self):
        """ run the application. this will block until stop() is called """
        # TODO: convert this to use generators too?
        self.running = True
        while self.running:
            found_one_alive = False

            for client, clientdesc in self._clients.iteritems():
                if clientdesc.con is None:
                    clientdesc.con = client.connect()
                
                try:
                    clientdesc.con.next()
                except Exception, e:
                    print('client error %s' % e)
                    print(traceback.format_exc())
                    if clientdesc.autoreconnect:
                        clientdesc.con = None 
                        if isinstance(clientdesc.autoreconnect, (int, float)):
                            clientdesc.autoreconnect -= 1
                        found_one_alive = True
                    else:
                        clientdesc.con = False 
                else:
                    found_one_alive = True
                
            if not found_one_alive:
                print('nothing left alive... quiting')
                self.stop() 

            now = time.time()
            timers = self._timers[:]
            self._timers = []
            for target_time, cb in timers:
                if now > target_time:
                    print('calling timer cb %s' % cb)
                    cb()
                else:   
                    self._timers.append((target_time, cb))

            time.sleep(self.sleep_time)

    def stop(self):
        """ stop the application """
        self.running = False




