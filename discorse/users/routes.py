"""
Elastic License 2.0

Copyright Discorse and/or licensed to Discorse under one
or more contributor license agreements. Licensed under the Elastic License;
you may not use this file except in compliance with the Elastic License.
"""
import random
import secrets

import pyotp
from apiflask import APIBlueprint, HTTPError
from apiflask.schemas import EmptySchema
from argon2 import PasswordHasher, exceptions

from ..database import (
    Event,
    GatewaySessionLimit,
    Member,
    Note,
    NotFound,
    RecoveryCode,
    Settings,
    User,
    create_token,
    dispatch_event,
    objectify,
    verify_token,
)
from ..ratelimiter import limiter
from .schemas import (
    Authorization,
    AuthorizationObject,
    CreateToken,
    CreateTokenObject,
    CreateUser,
    CreateUserObject,
    EditUser,
    EditUserObject,
    Gateway,
)
from .schemas import Note as NoteSchema
from .schemas import NoteObject, Register, UserObject

users = APIBlueprint('users', __name__)
registerr = APIBlueprint('register', 'discorse.register')
hasher = PasswordHasher()


def new_code() -> str:
    return secrets.token_hex(70)


def is_too_used(username: str):
    used: int = User.objects(User.username == username).count()

    if used > 9000:
        raise HTTPError(400, 'Too many people are using this username.')


def roll_discriminator() -> str:
    discriminator_number = random.randint(1, 9999)
    return '%04d' % discriminator_number


def get_recoveries(user_id: int) -> list[str]:
    codes: list[RecoveryCode] = RecoveryCode.objects(
        RecoveryCode.user_id == user_id
    ).all()
    return [c.code for c in codes]


def get_available_discriminator(username: str):
    is_too_used(username=username)

    for _ in range(10):
        discriminator = roll_discriminator()

        try:
            User.objects(
                User.username == username, User.discriminator == discriminator
            ).get()
        except NotFound:
            return discriminator
        else:
            continue

    return None


def is_available(username: str, discriminator: int):
    try:
        User.objects(
            User.username == username, User.discriminator == discriminator
        ).get()
    except NotFound:
        return
    else:
        raise HTTPError(400, 'Discriminator is already taken')


def authorize(
    token: str | None,
    fields: list[str] | None = None,
    rm_fields: list[str] | str | None = None,
) -> User:
    return verify_token(token=token, fields=fields, rm_fields=rm_fields)


def verify_mfa(user_id: int, code: int | str | None) -> None:
    setting: Settings = (
        Settings.objects(Settings.user_id == user_id)
        .only(['mfa_enabled', 'mfa_code'])
        .get()
    )

    recoveries = get_recoveries(user_id=user_id)

    if setting.mfa_enabled:
        totp = pyotp.TOTP(setting.mfa_code)

        if not code:
            raise HTTPError(403, 'mfa_code is a required field for users with mfa.')

        if code not in recoveries or code != totp.now():
            raise HTTPError(403, 'mfa code is invalid.')


def create_user(json: CreateUser) -> User:
    try:
        User.objects(User.email == json['email']).get()
    except NotFound:
        pass
    else:
        raise HTTPError(400, 'This email is already used.')

    discriminator = get_available_discriminator(username=json['username'])

    if discriminator is None:
        raise HTTPError(400, 'Too many people are using this username.')

    password = hasher.hash(json['password'])

    user: User = User.create(
        username=json['username'],
        email=json['email'],
        password=password,
        discriminator=discriminator,
    )
    Settings.create(user_id=user.id)

    return user


@registerr.post('/register')
@limiter.limit('2/hour')
@registerr.input(CreateUser)
@registerr.output(
    Register, 201, description='The token which you will use for authentication'
)
@registerr.doc(tag='Users')
def register(json: CreateUserObject):
    user = create_user(json=json)

    return {'token': create_token(user_id=user.id, user_password=user.password)}


@users.get('/users/@me')
@users.input(Authorization, 'headers')
@users.output(UserObject)
@users.doc(tag='Users')
def get_me(headers: AuthorizationObject):
    me = authorize(headers['authorization'], rm_fields=['password'])

    if me.verified is None:
        me = me.update(verified=False)

    return dict(me)


@users.post('/login')
@users.input(CreateToken)
@users.output(Register)
@users.doc(tag='Users')
def login(json: CreateTokenObject):
    try:
        with_pswd: User = (
            User.objects(User.email == json['email']).only(['password', 'id']).get()
        )
    except NotFound:
        raise HTTPError(400, 'Invalid email or password')

    try:
        hasher.verify(with_pswd.password, json['password'])
    except exceptions.VerifyMismatchError:
        raise HTTPError(400, 'Invalid email or password')

    verify_mfa(user_id=with_pswd.id, code=json.get('code'))

    return {
        'token': create_token(user_id=with_pswd.id, user_password=with_pswd.password)
    }


@users.patch('/users/@me')
@limiter.limit('10/second')
@users.input(EditUser)
@users.input(Authorization, 'headers')
@users.output(UserObject)
@users.doc(tag='Users')
def edit_me(json: EditUserObject, headers: AuthorizationObject):
    user = authorize(headers['authorization'])

    email = json.get('email')
    discriminator = json.get('discriminator')
    username = json.get('username')
    password = json.get('password')

    query = {}

    if email:
        query['email'] = email

    if discriminator:
        is_available(username=user.username, discriminator=discriminator)

        query['discriminator'] = discriminator

    if username:
        is_available(
            username=username, discriminator=discriminator or user.discriminator
        )

        query['username'] = username

    if password:
        query['password'] = hasher.hash(password=password)

        dispatch_event(
            'security',
            Event(
                'USER_INTERNAL_DISCONNECT', {'type': 'password-change'}, user_id=user.id
            ),
        )

    update = user.update(**query)
    ret = dict(update)
    return objectify(ret)


@users.get('/gateway')
@users.input(Authorization, 'headers')
@users.output(Gateway)
@users.doc(tag='Users')
def get_gateway(headers: AuthorizationObject):
    user = authorize(headers['authorization'], ['id'])

    try:
        gateway_session_limit = dict(
            GatewaySessionLimit.objects(GatewaySessionLimit.user_id == user.id)
            .defer(['user_id'])
            .get()
        )
    except NotFound:
        gateway_session_limit = dict(GatewaySessionLimit.create(user_id=user.id))
        gateway_session_limit.pop('user_id')

    if user.bot:
        guild_count = Member.objects(Member.user_id == user.id).count()

        shards = max(int(guild_count / 1000), 1)
    else:
        shards = 1

    return {
        'url': 'wss://gateway.derailed.one',
        'shards': shards,
        'session_start_limit': gateway_session_limit,
    }


@users.get('/users/@me/notes')
@users.input(Authorization, 'headers')
@users.output(NoteSchema(many=True))
@users.doc(tag='Notes')
async def get_notes(headers: AuthorizationObject):
    user = authorize(headers['authorization'], ['id'])

    notes = Note.objects(Note.creator_id == user.id).defer(['creator_id']).all()

    return [objectify(dict(note)) for note in notes]


@users.get('/users/@me/notes/<int:user_id>')
@users.input(Authorization, 'headers')
@users.output(NoteSchema)
@users.doc(tag='Notes')
async def get_note(user_id: int, headers: AuthorizationObject):
    user = authorize(headers['authorization'], ['id'])

    try:
        note = (
            Note.objects(Note.creator_id == user.id, Note.user_id == user_id)
            .defer(['creator_id'])
            .get()
        )
    except NotFound:
        raise HTTPError(404, 'Note for user not found')

    return objectify(dict(note))


@users.post('/users/@me/notes')
@users.input(Authorization, 'headers')
@users.input(NoteSchema, 'json')
@users.output(EmptySchema)
@users.doc(tag='Notes')
async def create_note(headers: AuthorizationObject, json: NoteObject):
    user = authorize(headers['authorization'], ['id'])

    try:
        User.objects(User.id == json['user_id']).only(['id']).get()
    except NotFound:
        raise HTTPError(400, 'User does not exist')

    try:
        note: Note = (
            Note.objects(Note.creator_id == user.id, Note.user_id == json['user_id'])
            .defer(['creator_id'])
            .get()
        )
    except NotFound:
        Note.create(
            creator_id=user.id, user_id=json['user_id'], content=json['content']
        )
    else:
        note.update(content=json['content'])
