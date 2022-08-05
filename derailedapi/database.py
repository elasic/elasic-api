"""
Copyright 2021-2022 Derailed.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
import base64
import binascii
import os
from typing import Any, TypeVar

import itsdangerous
import msgspec
from apiflask import HTTPError
from cassandra.auth import PlainTextAuthProvider
from cassandra.cqlengine import columns, connection, management, models
from cassandra.io import asyncorereactor, geventreactor

from derailedapi.enforgement import forger
from derailedapi.enums import (
    ContentFilterLevel,
    MFALevel,
    NotificationLevel,
    NSFWLevel,
    VerificationLevel,
)

auth_provider = PlainTextAuthProvider(
    os.getenv('SCYLLA_USER'), os.getenv('SCYLLA_PASSWORD')
)
T = TypeVar('T', dict[str, Any], list[Any])


def get_hosts():
    hs = os.getenv('SCYLLA_HOSTS')

    return None if hs is None else hs.split(',')


def connect():
    connection_class = (
        geventreactor.GeventConnection
        if os.getenv('GEVENT') == 'true'
        else asyncorereactor.AsyncoreConnection
    )

    connection.setup(
        get_hosts(),
        'derailed',
        auth_provider=auth_provider,
        connect_timeout=100,
        retry_connect=True,
        connection_class=connection_class,
    )


class Event(msgspec.Struct):
    name: str
    data: dict
    guild_id: int | None = None
    user_id: int | None = None
    user_ids: list[int] | None = None


class User(models.Model):
    __table_name__ = 'users'
    id: int = columns.BigInt(primary_key=True, partition_key=True, default=forger.forge)
    email: str = columns.Text(index=True)
    password: str = columns.Text()
    username: str = columns.Text(index=True)
    discriminator: str = columns.Text()
    avatar: str = columns.Text(default='')
    banner: str = columns.Text(default='')
    flags: int = columns.Integer(default=0)
    bot: bool = columns.Boolean(default=False)
    verified: bool | None = columns.Boolean(default=False)


class GuildPosition(models.Model):
    __table_name__ = 'guild_positions'
    user_id: int = columns.BigInt(primary_key=True)
    guild_id: int = columns.BigInt()
    folder: str = columns.Text()
    position: int = columns.Integer()


class Settings(models.Model):
    __table_name__ = 'settings'
    user_id: int = columns.BigInt(primary_key=True)
    locale: str = columns.Text(default='en-US')
    developer_mode: bool = columns.Boolean(default=False)
    theme: str = columns.Text(default='dark')
    status: str = columns.Text(default='invisible')
    mfa_enabled: bool = columns.Boolean(default=False)
    mfa_code: str = columns.Text()
    friend_requests_off: bool = columns.Boolean(default=False)


class RecoveryCode(models.Model):
    __table_name__ = 'recovery_codes'
    user_id: int = columns.BigInt(primary_key=True)
    code: str = columns.Text()


# NOTE:
# Max Outgoing Friend Requests and Incoming is 2000.
# Max Friends is set to 4000.
class Relationship(models.Model):
    __table_name__ = 'relationships'
    # the user id who created the relationship
    user_id: int = columns.BigInt(primary_key=True)
    # the user id who friended/blocked/etc the relationship
    target_id: int = columns.BigInt(index=True)
    # the type of relationship
    type: int = columns.Integer()


class Activity(models.Model):
    __table_name__ = 'activities'
    user_id: int = columns.BigInt(primary_key=True)
    guild_id: int = columns.BigInt()
    type: int = columns.Integer(default=0)
    created_at: str = columns.DateTime()
    content: str = columns.Text()
    stream_url: str = columns.Text()
    emoji_id: int = columns.BigInt()


class Channel(models.Model):
    __table_name__ = 'channels'
    id: int = columns.BigInt(primary_key=True)
    type: int = columns.Integer()
    name: str = columns.Text()


class Recipient(models.Model):
    __table_name__ = 'recipients'
    channel_id: int = columns.BigInt(primary_key=True)
    user_id: int = columns.BigInt()


class DMChannel(models.Model):
    __table_name__ = 'dm_channels'
    channel_id: int = columns.BigInt(primary_key=True)
    last_message_id: int = columns.BigInt()


class GroupDMChannel(DMChannel):
    __table_name__ = 'group_dm_channels'

    channel_id: int = columns.BigInt(primary_key=True)
    icon: str = columns.Text()
    owner_id: int = columns.BigInt()


class GuildChannel(models.Model):
    channel_id: int = columns.BigInt(primary_key=True)
    guild_id: int = columns.BigInt(index=True)
    position: int = columns.Integer()
    parent_id: int = columns.BigInt()
    nsfw: bool = columns.Boolean(default=False)


class CategoryChannel(GuildChannel):
    __table_name__ = 'category_channels'


class TextChannel(GuildChannel):
    __table_name__ = 'guild_text_channels'

    rate_limit_per_user: int = columns.Integer(default=0)
    topic: str = columns.Text()
    last_message_id: int = columns.BigInt()


class Guild(models.Model):
    __table_name__ = 'guilds'
    id: int = columns.BigInt(primary_key=True)
    name: str = columns.Text()
    icon: str = columns.Text()
    splash: str = columns.Text()
    discovery_splash: str = columns.Text()
    owner_id: int = columns.BigInt()
    default_permissions: int = columns.BigInt()
    afk_channel_id: int = columns.BigInt()
    afk_timeout: int = columns.Integer()
    default_message_notification_level: int = columns.Integer(
        default=NotificationLevel.ALL
    )
    explicit_content_filter: int = columns.Integer(default=ContentFilterLevel.DISABLED)
    mfa_level: int = columns.Integer(default=MFALevel.NONE)
    system_channel_id: int = columns.BigInt()
    system_channel_flags: int = columns.Integer()
    rules_channel_id: int = columns.BigInt()
    max_presences: int = columns.Integer(default=10000)
    max_members: int = columns.Integer(default=4000)
    vanity_url_code: str = columns.Text()
    description: str = columns.Text()
    banner: str = columns.Text()
    preferred_locale: str = columns.Text()
    guild_updates_channel_id: int = columns.BigInt()
    nsfw_level: int = columns.Integer(default=NSFWLevel.UNKNOWN)
    verification_level: int = columns.Integer(default=VerificationLevel.NONE)


class Feature(models.Model):
    __table_name__ = 'features'
    guild_id: int = columns.BigInt(primary_key=True)
    value: str = columns.Text()


class Role(models.Model):
    __table_name__ = 'roles'
    id: int = columns.BigInt(primary_key=True)
    guild_id: int = columns.BigInt(primary_key=True)
    name: str = columns.Text()
    color: int = columns.Integer()
    viewable: bool = columns.Boolean()
    icon: str = columns.Text()
    unicode_emoji: str = columns.Text()
    position: int = columns.Integer()
    permissions: int = columns.BigInt()
    mentionable: bool = columns.Boolean()


class Member(models.Model):
    __table_name__ = 'members'
    guild_id: int = columns.BigInt(primary_key=True)
    user_id: int = columns.BigInt(index=True)
    nick: str = columns.Text()
    avatar: str = columns.Text()
    joined_at: str = columns.DateTime()
    deaf: bool = columns.Boolean()
    mute: bool = columns.Boolean()
    pending: bool = columns.Boolean()
    communication_disabled_until: str = columns.DateTime()
    owner: bool = columns.Boolean()


class MemberRole(models.Model):
    __table_name__ = 'member_roles'
    guild_id: int = columns.BigInt(primary_key=True)
    user_id: int = columns.BigInt(index=True)
    role_id: int = columns.BigInt()


class Ban(models.Model):
    __table_name__ = 'bans'
    guild_id: int = columns.BigInt(primary_key=True)
    user_id: int = columns.BigInt()
    reason: str = columns.Text()


class GatewaySessionLimit(models.Model):
    __table_name__ = 'gateway_session_limit'
    __options__ = {'default_time_to_live': 43200}
    user_id: int = columns.BigInt(primary_key=True)
    total: int = columns.Integer(default=1000)
    remaining: int = columns.Integer(default=1000)
    max_concurrency: int = columns.Integer(default=16)


def create_token(user_id: int, user_password: str) -> str:
    signer = itsdangerous.TimestampSigner(user_password)
    user_id = str(user_id)
    user_id = base64.b64encode(user_id.encode())

    return signer.sign(user_id).decode()


def verify_token(
    token: str | None,
    fields: list[str] | str | None = None,
    rm_fields: list[str] | str | None = None,
) -> User:
    if token is None:
        raise HTTPError(401, 'Authorization is invalid')

    fragmented = token.split('.')
    user_id = fragmented[0]

    try:
        user_id = base64.b64decode(user_id.encode())
        user_id = int(user_id)
    except (ValueError, binascii.Error):
        raise HTTPError(401, 'Failed to get container volume for Authorization')

    try:
        user: User = User.objects(User.id == user_id).only(['password']).get()
    except:
        raise HTTPError(401, 'Object for Authorization not found')

    signer = itsdangerous.TimestampSigner(user.password)

    try:
        signer.unsign(token)

        if fields is not None and not isinstance(fields, list):
            fields = list(fields)

        if rm_fields is not None and not isinstance(rm_fields, list):
            rm_fields = list(rm_fields)

        if fields in ['id', ['id']]:
            user.id = user_id
            return user
        elif fields and rm_fields:
            return User.objects(User.id == user_id).only(fields).defer(rm_fields).get()
        elif fields:
            return User.objects(User.id == user_id).only(fields).get()
        elif rm_fields:
            return User.objects(User.id == user_id).defer(rm_fields).get()
        else:
            return User.objects(User.id == user_id).get()

    except (itsdangerous.BadSignature):
        raise HTTPError(401, 'Signature on Authorization is Invalid')


def objectify(data: T) -> T:
    if isinstance(data, dict):
        for k, v in data.items():
            if (
                isinstance(v, int)
                and v > 2147483647
                or isinstance(v, int)
                and 'permissions' in k
            ):
                data[k] = str(v)
            elif isinstance(v, list):
                new_value = []

                for item in v:
                    if isinstance(item, (dict, list)):
                        new_value.append(objectify(item))
                    else:
                        new_value.append(item)

                data[k] = new_value

    elif isinstance(data, list):
        new_data = []

        for item in data:
            if isinstance(item, (dict, list)):
                new_data.append(objectify(item))
            else:
                new_data.append(item)

        data = new_data

    return data


def sync_tables():
    management.sync_table(User)
    management.sync_table(GuildPosition)
    management.sync_table(Settings)
    management.sync_table(RecoveryCode)
    management.sync_table(Relationship)
    management.sync_table(Activity)
    management.sync_table(Channel)
    management.sync_table(Recipient)
    management.sync_table(DMChannel)
    management.sync_table(GroupDMChannel)
    management.sync_table(CategoryChannel)
    management.sync_table(TextChannel)
    management.sync_table(Guild)
    management.sync_table(Feature)
    management.sync_table(Role)
    management.sync_table(Member)
    management.sync_table(MemberRole)
    management.sync_table(Ban)
    management.sync_table(GatewaySessionLimit)
