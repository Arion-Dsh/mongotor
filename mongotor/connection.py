# coding: utf-8
# <mongotor - An asynchronous driver and toolkit for accessing MongoDB with Tornado>
# Copyright (C) <2012>  Marcel Nicolay <marcel.nicolay@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from tornado import iostream, gen
from mongotor.errors import InterfaceError, IntegrityError
from mongotor import helpers
import socket
import logging
import struct

logger = logging.getLogger(__name__)


class Connection(object):

    def __init__(self, host, port, pool=None, autoreconnect=True, timeout=5):
        self._host = host
        self._port = port
        self._pool = pool
        self._autoreconnect = autoreconnect
        self._timeout = timeout
        self._connected = False

        self._connect()

        logger.debug('{} created'.format(self))

    def _connect(self):
        self.usage = 0
        try:
            socket.timeout(self._timeout)
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
            s.connect((self._host, self._port))

            self._stream = iostream.IOStream(s)
            self._stream.set_close_callback(self._socket_close)

            self._connected = True
        except socket.error, error:
            raise InterfaceError(error)

    def __repr__(self):
        return "Connection {} ::: ".format(id(self))

    def _parse_header(self, header, request_id):
        #logger.debug('got data %r' % header)
        length = int(struct.unpack("<i", header[:4])[0])
        _request_id = struct.unpack("<i", header[8:12])[0]

        assert _request_id == request_id, \
            "ids don't match %r %r" % (request_id, _request_id)

        operation = 1  # who knows why
        assert operation == struct.unpack("<i", header[12:])[0]
        #logger.debug('%s' % length)
        #logger.debug('waiting for another %d bytes' % (length - 16))

        return length

    def _parse_response(self, response, request_id):
        try:
            response = helpers._unpack_response(response, request_id)
        except Exception, e:
            logger.error('error %s' % e)
            return None, e

        if response and response['data'] and response['data'][0].get('err') \
            and response['data'][0].get('code'):

            logger.error(response['data'][0]['err'])
            return response, IntegrityError(response['data'][0]['err'],
                code=response['data'][0]['code'])

        #logger.debug('response: %s' % response)
        return response, None

    def _socket_close(self):
        logger.debug('{} connection stream closed'.format(self))
        self._connected = False
        self.release()

    def close(self):
        logger.debug('{} connection close'.format(self))
        self._connected = False
        self._stream.close()

    def closed(self):
        return not self._connected

    def release(self):
        if self._pool:
            self._pool.release(self)

    @gen.engine
    def send_message(self, message, callback):
        try:
            if self.closed():
                if self._autoreconnect:
                    self._connect()
                else:
                    raise InterfaceError('connection is closed and autoreconnect is false')

            response = yield gen.Task(self._send_message, message)
            callback(response)
        except IOError:
            logger.exception('{} problems sending message'.format(self))
            callback((None, InterfaceError('connection closed')))
        finally:
            self.release()

    @gen.engine
    def _send_message(self, message, callback=None):
        self.usage += 1

        (self.request_id, message) = message
        try:
            self._stream.write(message)

            header_data = yield gen.Task(self._stream.read_bytes, 16)
            length = self._parse_header(header_data, self.request_id)

            data = yield gen.Task(self._stream.read_bytes, length - 16)

            response, error = self._parse_response(data, self.request_id)

            if callback:
                callback((response, error))

        except IOError, e:
            self._connected = False
            raise e
