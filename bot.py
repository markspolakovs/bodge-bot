import json
from typing import List, Any, Set, Dict

import discord
import os
import aiohttp
from aiohttp import web


class GameChannel(object):
    guild: discord.Guild
    chan: discord.VoiceChannel
    client: 'Client'
    game_running = False
    is_meeting = False
    dead: Set[int] = set()
    commentators: Set[int] = set()

    def __init__(self, guild, chan, client) -> None:
        super().__init__()
        self.guild = guild
        self.chan = self.guild.get_channel(chan.id)
        self.client = client
        print("Channel members:", self.chan.voice_states)

    async def reset(self):
        self.dead = set()
        self.game_running = False
        self.is_meeting = False
        await self.sync_state()

    async def start_game(self):
        self.game_running = True
        await self.sync_state()

    async def kill(self, member_id: int):
        self.dead.add(member_id)
        await self.sync_state()

    async def unkill(self, member_id: int):
        self.dead.remove(member_id)
        await self.sync_state()

    async def start_meeting(self):
        self.is_meeting = True
        await self.sync_state()

    async def stop_meeting(self):
        self.is_meeting = False
        await self.sync_state()

    async def sync_state(self):
        await self.update_commentators()
        for member_id, state in self.chan.voice_states.items():
            should_be_muted = None
            should_be_deafened = None
            if member_id in self.commentators:
                if state.mute:
                    should_be_muted = False
                if state.deaf:
                    should_be_deafened = False
            else:
                if not self.game_running:
                    if state.mute:
                        should_be_muted = False
                    if state.deaf:
                        should_be_deafened = False
                else:
                    if member_id in self.dead:
                        if self.is_meeting:
                            if not state.mute:
                                should_be_muted = True
                            if not state.deaf:
                                should_be_deafened = True
                        else:
                            if state.mute:
                                should_be_muted = False
                            if state.deaf:
                                should_be_deafened = False
                    else:
                        if self.is_meeting:
                            if state.mute:
                                should_be_muted = False
                            if state.deaf:
                                should_be_deafened = False
                        else:
                            if not state.mute:
                                should_be_muted = True
                            if not state.deaf:
                                should_be_deafened = True

            if should_be_muted is not None or should_be_deafened is not None:
                kwargs = {}
                if should_be_muted is not None:
                    kwargs['mute'] = should_be_muted
                if should_be_deafened is not None:
                    kwargs['deafen'] = should_be_deafened

                if not (member := self.guild.get_member(member_id)):
                    member = await self.guild.fetch_member(member_id)
                await member.edit(
                    **kwargs,
                    reason='B0dge B0t sync'
                )

        # print("Sync done, sending WS.")
        await self.client.send_to_all({
            'kind': 'State/UPDATE',
            **self.get_state()
        })

    def get_state(self):
        return {
            'gameRunning': self.game_running,
            'isMeeting': self.is_meeting,
            'dead': [str(x) for x in self.dead],
            'commentators': [str(x) for x in self.commentators]
        }

    async def update_commentators(self):
        for member_id in self.chan.voice_states.keys():
            if not (member := self.guild.get_member(member_id)):
                member = await self.guild.fetch_member(member_id)  # type: discord.Member
            if 'commentator' in [role.name.lower() for role in member.roles]:
                self.commentators.add(member_id)
                continue
            # not a commentator
            if member_id in self.commentators:
                self.commentators.remove(member_id)
        for comm_id in self.commentators:
            if comm_id not in self.chan.voice_states:
                self.commentators.remove(comm_id)

    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState) -> None:
        if before.channel is None and after.channel.id == self.chan.id:
            # just joined channel
            await self.client.send_to_all({
                'kind': 'Channel/JOINED',
                'member': {
                    'id': str(member.id),
                    'name': member.display_name,
                    'disc': member.discriminator,
                    'avatar_hash': member.avatar
                }
            })
            await self.sync_state()
        elif after.channel is None or after.channel.id != self.chan.id:
            # left channel
            await self.client.send_to_all({
                'kind': 'Channel/LEFT',
                'member': {
                    'id': str(member.id)
                }
            })
            await self.sync_state()
        else:
            # state change
            pass

    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.roles != after.roles:
            await self.sync_state()


class Client(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.voice_states = True
        intents.members = True
        member_cache_flags = discord.MemberCacheFlags.none()
        member_cache_flags.voice = True

        super().__init__(intents=intents, member_cache_flags=member_cache_flags)

        self.app = web.Application()
        self.app.add_routes([web.get('/socket', self.on_websocket)])

        self.app.add_routes([
            web.get('/control/getState', self.web_get_state),
            web.get('/control/startGame', self.web_start_game),
            web.get('/control/startMeeting', self.web_start_meeting),
            web.get('/control/endMeeting', self.web_end_meeting),
            web.get('/control/kill', self.web_kill),
            web.get('/control/unkill', self.web_unkill),
            web.get('/control/reset', self.web_reset),
        ])

        self.app.add_routes([
            web.static('/', 'client/')
        ])

        self.subs: Dict[str, GameChannel] = dict()
        self.socks: List[web.WebSocketResponse] = list()

    async def web_get_state(self, request: web.Request):
        sub = self.subs[os.getenv("DISCORD_MONITORED_CHANNEL_ID")]
        return web.Response(body=json.dumps(sub.get_state()))

    async def web_start_game(self, request: web.Request):
        sub = self.subs[os.getenv("DISCORD_MONITORED_CHANNEL_ID")]
        await sub.start_game()
        return web.Response(body=json.dumps(sub.get_state()))

    async def web_start_meeting(self, request: web.Request):
        sub = self.subs[os.getenv("DISCORD_MONITORED_CHANNEL_ID")]
        await sub.start_meeting()
        return web.Response(body=json.dumps(sub.get_state()))

    async def web_end_meeting(self, request: web.Request):
        sub = self.subs[os.getenv("DISCORD_MONITORED_CHANNEL_ID")]
        await sub.stop_meeting()
        return web.Response(body=json.dumps(sub.get_state()))

    async def web_kill(self, req: web.Request):
        sub = self.subs[os.getenv("DISCORD_MONITORED_CHANNEL_ID")]
        if "id" not in req.query:
            return web.Response(status=400, body='')
        await sub.kill(int(req.query["id"]))
        return web.Response(body=json.dumps(sub.get_state()))

    async def web_unkill(self, req: web.Request):
        sub = self.subs[os.getenv("DISCORD_MONITORED_CHANNEL_ID")]
        if "id" not in req.query:
            return web.Response(status=400, body='')
        await sub.unkill(int(req.query["id"]))
        return web.Response(body=json.dumps(sub.get_state()))

    async def web_reset(self, request: web.Request):
        sub = self.subs[os.getenv("DISCORD_MONITORED_CHANNEL_ID")]
        await sub.reset()
        return web.Response(body=json.dumps(sub.get_state()))

    async def on_websocket(self, request: web.Request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        await ws.send_json({
            'kind': 'App/HELLO'
        })

        sub = self.subs[os.getenv("DISCORD_MONITORED_CHANNEL_ID")]
        await ws.send_json({
            'kind': 'Channel/SYNC_MEMBERS',
            'members': [{
                'id': str(member.id),
                'name': member.display_name,
                'disc': member.discriminator,
                'avatar_hash': member.avatar
            } for member in sub.chan.members]
        })
        await ws.send_json({
            'kind': 'State/UPDATE',
            **sub.get_state()
        })

        self.socks.append(ws)

        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.ERROR:
                print("WS error", msg)

    async def on_ready(self):
        print("Logged on as {}!".format(self.user))
        add_url = discord.utils.oauth_url(
            os.getenv("DISCORD_CLIENT_ID"),
            discord.Permissions(29361152),
            None,
            os.getenv("BASE_URL") + "/oauth/redirect"
        )
        print("Guilds:", self.guilds)
        for guild in self.guilds:  # type: discord.Guild
            for chan in guild.voice_channels:
                print("Found chan {}".format(chan.id))
                if str(chan.id) == os.getenv("DISCORD_MONITORED_CHANNEL_ID"):
                    print("Chunking members, please wait...")
                    await guild.chunk(cache=True)
                    print("Chunk complete, guild members: {}".format(len(guild.members)))
                    sub = GameChannel(guild, chan, self)
                    await sub.sync_state()
                    self.subs[str(chan.id)] = sub

        print("Add this bot to your server here: {}".format(add_url))

    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState):
        if str(before.channel.id if before.channel is not None else "NOPE") in self.subs \
                or str(after.channel.id if after.channel is not None else "NOPE") in self.subs:
            await self.subs[str(after.channel.id if after.channel is not None else before.channel.id)]\
                .on_voice_state_update(member, before, after)

    async def on_member_update(self, before: discord.Member, after: discord.Member):
        for sub in self.subs.values():
            if member := sub.guild.get_member(after.id) is None:
                member = await sub.guild.fetch_member(after.id)
            if member is not None:
                await sub.on_member_update(before, after)

    async def send_to_all(self, msg: Any):
        for sock in self.socks:
            await sock.send_json(msg)

    async def run_web(self):
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, os.getenv("HOST"), int(os.getenv("PORT")))
        await site.start()


if __name__ == '__main__':
    print("Starting B0dge B0t...")
    import dotenv, asyncio

    dotenv.load_dotenv()
    bot = Client()
    asyncio.get_event_loop().run_until_complete(
        asyncio.gather(
            bot.start(os.getenv("DISCORD_BOT_TOKEN")),
            bot.run_web()
        )
    )
    asyncio.get_event_loop().run_forever()
