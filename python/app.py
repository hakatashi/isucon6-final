# -*- coding: utf-8 -*-
import json
import os
from datetime import timedelta, tzinfo
import time

import MySQLdb.cursors

from flask import Flask, jsonify, request, Response, redirect, url_for

#####################################################
from __future__ import unicode_literals

from collections import OrderedDict
from flask import Blueprint, request, current_app, json, stream_with_context
from redis import StrictRedis
import six

@six.python_2_unicode_compatible
class Message(object):
    """
    Data that is published as a server-sent event.
    """
    def __init__(self, data, type=None, id=None, retry=None):
        """
        Create a server-sent event.

        :param data: The event data. If it is not a string, it will be
            serialized to JSON using the Flask application's
            :class:`~flask.json.JSONEncoder`.
        :param type: An optional event type.
        :param id: An optional event ID.
        :param retry: An optional integer, to specify the reconnect time for
            disconnected clients of this stream.
        """
        self.data = data
        self.type = type
        self.id = id
        self.retry = retry

    def to_dict(self):
        """
        Serialize this object to a minimal dictionary, for storing in Redis.
        """
        # data is required, all others are optional
        d = {"data": self.data}
        if self.type:
            d["type"] = self.type
        if self.id:
            d["id"] = self.id
        if self.retry:
            d["retry"] = self.retry
        return d

    def __str__(self):
        """
        Serialize this object to a string, according to the `server-sent events
        specification <https://www.w3.org/TR/eventsource/>`_.
        """
        if isinstance(self.data, six.string_types):
            data = self.data
        else:
            data = json.dumps(self.data)
        lines = ["data:{value}".format(value=line) for line in data.splitlines()]
        if self.type:
            lines.insert(0, "event:{value}".format(value=self.type))
        if self.id:
            lines.append("id:{value}".format(value=self.id))
        if self.retry:
            lines.append("retry:{value}".format(value=self.retry))
        return "\n".join(lines) + "\n\n"

    def __repr__(self):
        kwargs = OrderedDict()
        if self.type:
            kwargs["type"] = self.type
        if self.id:
            kwargs["id"] = self.id
        if self.retry:
            kwargs["retry"] = self.retry
        kwargs_repr = "".join(
            ", {key}={value!r}".format(key=key, value=value)
            for key, value in kwargs.items()
        )
        return "{classname}({data!r}{kwargs})".format(
            classname=self.__class__.__name__,
            data=self.data,
            kwargs=kwargs_repr,
        )

    def __eq__(self, other):
        return (
            isinstance(other, self.__class__) and
            self.data == other.data and
            self.type == other.type and
            self.id == other.id and
            self.retry == other.retry
        )


class ServerSentEventsBlueprint(Blueprint):
    """
    A :class:`flask.Blueprint` subclass that knows how to publish, subscribe to,
    and stream server-sent events.
    """
    @property
    def redis(self):
        """
        A :class:`redis.StrictRedis` instance, configured to connect to the
        current application's Redis server.
        """
        redis_url = current_app.config.get("SSE_REDIS_URL")
        if not redis_url:
            redis_url = current_app.config.get("REDIS_URL")
        if not redis_url:
            raise KeyError("Must set a redis connection URL in app config.")
        return StrictRedis.from_url(redis_url)

    def publish(self, data, type=None, id=None, retry=None, channel='sse'):
        """
        Publish data as a server-sent event.

        :param data: The event data. If it is not a string, it will be
            serialized to JSON using the Flask application's
            :class:`~flask.json.JSONEncoder`.
        :param type: An optional event type.
        :param id: An optional event ID.
        :param retry: An optional integer, to specify the reconnect time for
            disconnected clients of this stream.
        :param channel: If you want to direct different events to different
            clients, you may specify a channel for this event to go to.
            Only clients listening to the same channel will receive this event.
            Defaults to "sse".
        """
        message = Message(data, type=type, id=id, retry=retry)
        msg_json = json.dumps(message.to_dict())
        return self.redis.publish(channel=channel, message=msg_json)

    def messages(self, channel='sse'):
        """
        A generator of :class:`~flask_sse.Message` objects from the given channel.
        """
        pubsub = self.redis.pubsub()
        pubsub.subscribe(channel)
        for pubsub_message in pubsub.listen():
            if pubsub_message['type'] == 'message':
                msg_dict = json.loads(pubsub_message['data'])
                yield Message(**msg_dict)

    def stream(self):
        """
        A view function that streams server-sent events. Ignores any
        :mailheader:`Last-Event-ID` headers in the HTTP request.
        Use a "channel" query parameter to stream events from a different
        channel than the default channel (which is "sse").
        """
        channel = request.args.get('channel') or 'sse'
        
        db = get_db()
        last_update = time.time()
        token = check_token(db, request.headers.get('x-csrf-token'))

        @stream_with_context
        def generator():
            for message in self.messages(channel=channel):
                message_str = str(message)

                if message.id and time.time() - last_update > 1.0:
                    update_room_watcher(db, int(message.id), token['id'])
                    watcher_count = get_watcher_count(db, int(message.id))
                    message_str += str(Message(watcher_count, type='watcher_count'))
                    last_update = time.time()

                yield message_str

        return current_app.response_class(
            generator(),
            mimetype='text/event-stream',
        )


sse = ServerSentEventsBlueprint('sse', __name__)
"""
An instance of :class:`~flask_sse.ServerSentEventsBlueprint`
that hooks up the :meth:`~flask_sse.ServerSentEventsBlueprint.stream`
method as a view function at the root of the blueprint. If you don't
want to customize this blueprint at all, you can simply import and
use this instance in your application.
"""
sse.add_url_rule(rule="", endpoint="stream", view_func=sse.stream)
#####################################################

def get_db():
    host = os.environ.get('MYSQL_HOST', 'localhost')
    port = int(os.environ.get('MYSQL_PORT', 3306))
    user = os.environ.get('MYSQL_USER', 'root')
    passwd = os.environ.get('MYSQL_PASS', '')
    dbname = 'isuketch'
    charset = 'utf8mb4'
    cursorclass = MySQLdb.cursors.DictCursor
    autocommit = True
    return MySQLdb.connect(host=host, port=port, user=user, passwd=passwd, db=dbname, cursorclass=cursorclass, charset=charset, autocommit=autocommit)


def execute(db, sql, params={}):
    cursor = db.cursor()
    cursor.execute(sql, params)
    return cursor.lastrowid


def select_one(db, sql, params={}):
    cursor = db.cursor()
    cursor.execute(sql, params)
    return cursor.fetchone()


def select_all(db, sql, params={}):
    cursor = db.cursor()
    cursor.execute(sql, params)
    return cursor.fetchall()


def print_and_flush(content):
    return content


def type_cast_point_data(data):
    return {
        'id': int(data['id']),
        'stroke_id': int(data['stroke_id']),
        'x': float(data['x']),
        'y': float(data['y']),
    }


class UTC(tzinfo):
    def utcoffset(self, dt):
        return timedelta(0)

    def tzname(self, dt):
        return "UTC"

    def dst(self, dt):
        return timedelta(0)


def to_RFC3339_micro(date):
    # RFC3339では+00:00のときはZにするという仕様だが、pythonは準拠していないため
    return date.replace(tzinfo=UTC()).isoformat().replace('+00:00', 'Z')


def type_cast_stroke_data(data):
    return {
        'id': int(data['id']),
        'room_id': int(data['room_id']),
        'width': int(data['width']),
        'red': int(data['red']),
        'green': int(data['green']),
        'blue': int(data['blue']),
        'alpha': float(data['alpha']),
        'points': list(map(type_cast_point_data, data['points'])) if 'points' in data and data['points'] else [],
        'created_at': to_RFC3339_micro(data['created_at']) if data['created_at'] else '',
    }


def type_cast_room_data(data):
    return {
        'id': int(data['id']),
        'name': data['name'],
        'canvas_width': int(data['canvas_width']),
        'canvas_height': int(data['canvas_height']),
        'created_at': to_RFC3339_micro(data['created_at']) if data['created_at'] else '',
        'strokes': list(map(type_cast_stroke_data, data['strokes'])) if 'strokes' in data and data['strokes'] else [],
        'stroke_count': int(data.get('stroke_count', 0)),
        'watcher_count': int(data.get('watcher_count', 0)),
    }


class TokenException(Exception):
    pass


def check_token(db, csrf_token):
    sql = 'SELECT `id`, `csrf_token`, `created_at` FROM `tokens`'
    sql += ' WHERE `csrf_token` = %(csrf_token)s AND `created_at` > CURRENT_TIMESTAMP(6) - INTERVAL 1 DAY'
    token = select_one(db, sql, {'csrf_token': csrf_token})
    if not token:
        raise TokenException()
    return token


def get_stroke_points(db, stroke_id):
    sql = 'SELECT `id`, `stroke_id`, `x`, `y` FROM `points` WHERE `stroke_id` = %(stroke_id)s ORDER BY `id` ASC'
    return select_all(db, sql, {'stroke_id': stroke_id})


def get_strokes(db, room_id, greater_than_id):
    sql = 'SELECT `id`, `room_id`, `width`, `red`, `green`, `blue`, `alpha`, `created_at` FROM `strokes`'
    sql += ' WHERE `room_id` = %(room_id)s AND `id` > %(greater_than_id)s ORDER BY `id` ASC'
    return select_all(db, sql, {'room_id': room_id, 'greater_than_id': greater_than_id})


def get_room(db, room_id):
    sql = 'SELECT `id`, `name`, `canvas_width`, `canvas_height`, `created_at` FROM `rooms` WHERE `id` = %(room_id)s'
    return select_one(db, sql, {'room_id': room_id})


def get_watcher_count(db, room_id):
    sql = 'SELECT COUNT(*) AS `watcher_count` FROM `room_watchers`'
    sql += ' WHERE `room_id` = %(room_id)s AND `updated_at` > CURRENT_TIMESTAMP(6) - INTERVAL 3 SECOND'
    result = select_one(db, sql, {'room_id': room_id})
    return result['watcher_count']


def update_room_watcher(db, room_id, token_id):
    sql = 'INSERT INTO `room_watchers` (`room_id`, `token_id`) VALUES (%(room_id)s, %(token_id)s)'
    sql += ' ON DUPLICATE KEY UPDATE `updated_at` = CURRENT_TIMESTAMP(6)'
    return execute(db, sql, {'room_id': room_id, 'token_id': token_id})


app = Flask(__name__)
app.config['JSONIFY_PRETTYPRINT_REGULAR'] = False
app.config['DEBUG'] = os.environ.get('ISUCON_ENV') != 'production'
app.config['REDIS_URL'] = 'redis://' + os.environ.get('REDIS_URL')
app.register_blueprint(sse, url_prefix = '/api/sse')

# Routes


@app.route('/api/csrf_token', methods=['POST'])
def post_api_csrf_token():
    db = get_db()

    sql = 'INSERT INTO `tokens` (`csrf_token`) VALUES'
    sql += ' (SHA2(CONCAT(RAND(), UUID_SHORT()), 256))'

    id = execute(db, sql)

    sql = 'SELECT `id`, `csrf_token`, `created_at` FROM `tokens` WHERE id = %(id)s'
    token = select_one(db, sql, {'id': id})

    return jsonify({'token': token['csrf_token']})


@app.route('/api/rooms', methods=['GET'])
def get_api_rooms():

    db = get_db()

    sql = 'SELECT `room_id`, MAX(`id`) AS `max_id` FROM `strokes`'
    sql += ' GROUP BY `room_id` ORDER BY `max_id` DESC LIMIT 100'
    results = select_all(db, sql)

    rooms = []
    for result in results:
        room = get_room(db, result['room_id'])
        strokes = get_strokes(db, room['id'], 0)
        room['stroke_count'] = len(strokes)
        rooms.append(room)

    return jsonify({'rooms': list(map(type_cast_room_data, rooms))})


@app.route('/api/rooms', methods=['POST'])
def post_api_rooms():
    db = get_db()
    try:
        token = check_token(db, request.headers.get('x-csrf-token'))
    except TokenException:
        res = jsonify({'error': 'トークンエラー。ページを再読み込みしてください。'})
        res.status_code = 400
        return res

    posted_room = request.get_json()
    if 'name' not in posted_room or 'canvas_width' not in posted_room or 'canvas_height' not in posted_room:
        res = jsonify({'error': 'リクエストが正しくありません。'})
        res.status_code = 400
        return res

    cursor = db.cursor()
    cursor.connection.autocommit(False)
    try:
        sql = 'INSERT INTO `rooms` (`name`, `canvas_width`, `canvas_height`)'
        sql += ' VALUES (%(name)s, %(canvas_width)s, %(canvas_height)s)'
        cursor.execute(sql, {
            'name': posted_room.get('name'),
            'canvas_width': posted_room.get('canvas_width'),
            'canvas_height': posted_room.get('canvas_height'),
        })
        room_id = cursor.lastrowid

        sql = 'INSERT INTO `room_owners` (`room_id`, `token_id`) VALUES (%(room_id)s, %(token_id)s)'
        cursor.execute(sql, {
            'room_id': room_id,
            'token_id': token['id'],
        })
        cursor.connection.commit()
    except Exception as e:
        cursor.connection.rollback()
        app.logger.error(e)
        res = jsonify({'error': 'エラーが発生しました。'})
        res.status_code = 500
        return res
    else:
        cursor.connection.autocommit(True)

    room = get_room(db, room_id)
    return jsonify({'room': type_cast_room_data(room)})


@app.route('/api/rooms/<id>')
def get_api_rooms_id(id):
    db = get_db()
    room = get_room(db, id)

    if room is None:
        res = jsonify({'error': 'この部屋は存在しません。'})
        res.status_code = 500
        return res

    strokes = get_strokes(db, room['id'], 0)

    for i, stroke in enumerate(strokes):
        strokes[i]['points'] = get_stroke_points(db, stroke['id'])

    room['strokes'] = strokes
    room['watcher_count'] = get_watcher_count(db, room['id'])

    return jsonify({'room': type_cast_room_data(room)})


@app.route('/api/stream/rooms/<id>')
def get_api_stream_rooms_id(id):
    db = get_db()

    try:
        token = check_token(db, request.args.get('csrf_token'))
    except TokenException:
        return print_and_flush(
            'event:bad_request\n' +
            'data:トークンエラー。ページを再読み込みしてください。\n\n'
        ), 200, {'Content-Type': 'text/event-stream'}

    room = get_room(db, id)

    if room is None:
        return print_and_flush(
            'event:bad_request\n' +
            'data:この部屋は存在しません\n\n'
        ), 200, {'Content-Type': 'text/event-stream'}

    last_stroke_id = 0
    if 'Last-Event-ID' in request.headers:
        return redirect(url_for('sse.stream', channel = str(room['id'])))

    update_room_watcher(db, room['id'], token['id'])
    watcher_count = get_watcher_count(db, room['id'])

    sse.publish(watcher_count, type = 'watcher_count', channel = str(room['id']))

    response = \
        'retry:500\n\n' + \
        'event:watcher_count\n' + \
        'data:%d\n\n' % (watcher_count)

    strokes = get_strokes(db, room['id'], last_stroke_id)
    for stroke in strokes:
        response += \
            'id:' + str(stroke['id']) + '\n\n' + \
            'event:stroke\n' + \
            'data:' + json.dumps(type_cast_stroke_data(stroke)) + '\n\n'

    return Response(response, mimetype='text/event-stream')

@app.route('/api/strokes/rooms/<id>', methods=['POST'])
def post_api_strokes_rooms_id(id):
    db = get_db()

    try:
        token = check_token(db, request.headers.get('x-csrf-token'))
    except TokenException:
        res = jsonify({'error': 'トークンエラー。ページを再読み込みしてください。'})
        res.status_code = 400
        return res

    room = get_room(db, id)

    if room is None:
        res = jsonify({'error': 'この部屋は存在しません。'})
        res.status_code = 404
        return res

    posted_stroke = request.get_json()
    if 'width' not in posted_stroke or 'points' not in posted_stroke:
        res = jsonify({'error': 'リクエストが正しくありません。'})
        res.status_code = 400
        return res

    strokes = get_strokes(db, room['id'], 0)
    if len(strokes) == 0:
        sql = 'SELECT COUNT(*) AS cnt FROM `room_owners` WHERE `room_id` = %(room_id)s AND `token_id` = %(token_id)s'
        result = select_one(db, sql, {'room_id': room['id'], 'token_id': token['id']})
        if result['cnt'] == 0:
            res = jsonify({'error': '他人の作成した部屋に1画目を描くことはできません'})
            res.status_code = 400
            return res

    cursor = db.cursor()
    cursor.connection.autocommit(False)
    try:
        sql = 'INSERT INTO `strokes` (`room_id`, `width`, `red`, `green`, `blue`, `alpha`)'
        sql += ' VALUES(%(room_id)s, %(width)s, %(red)s, %(green)s, %(blue)s, %(alpha)s)'
        cursor.execute(sql, {
            'room_id': room['id'],
            'width': posted_stroke.get('width'),
            'red': posted_stroke.get('red'),
            'green': posted_stroke.get('green'),
            'blue': posted_stroke.get('blue'),
            'alpha': posted_stroke.get('alpha'),
        })
        stroke_id = cursor.lastrowid

        sql = 'INSERT INTO `points` (`stroke_id`, `x`, `y`) VALUES (%(stroke_id)s, %(x)s, %(y)s)'
        for point in posted_stroke.get('points'):
            cursor.execute(sql, {
                'stroke_id': stroke_id,
                'x': point['x'],
                'y': point['y']
            })
        cursor.connection.commit()
    except Exception as e:
        cursor.connection.rollback()
        app.logger.error(e)
        res = jsonify({'error': 'エラーが発生しました。'})
        res.status_code = 500
        return res
    else:
        cursor.connection.autocommit(True)

    sql = 'SELECT `id`, `room_id`, `width`, `red`, `green`, `blue`, `alpha`, `created_at` FROM `strokes`'
    sql += ' WHERE `id` = %(stroke_id)s'
    stroke = select_one(db, sql, {'stroke_id': stroke_id})

    stroke['points'] = get_stroke_points(db, stroke_id)
    sse.publish(type_cast_stroke_data(stroke), type = 'stroke', id = str(stroke['id']), channel = str(room['id']))

    return jsonify({'stroke': type_cast_stroke_data(stroke)})


if __name__ == '__main__':
    debug = os.environ.get('ISUCON_ENV') != 'production'
    app.run(host='', port=80)
