from typing import List, Any, Set, Dict

import discord
import os
from aiohttp import web


class GameChannel(object):
    guild: discord.Guild
    chan: discord.VoiceChannel
    client: 'Client'
    game_running = False
    is_meeting = False
    dead: Set[str] = set()
    ignored: Set[str] = set()

    def __init__(self, guild, chan, client) -> None:
        super().__init__()
        self.guild = guild
        self.chan = chan
        self.client = client
        print("Channel members:", self.chan.voice_states)

    async def sync_state(self):
        for id, state in self.chan.voice_states.items():
            if id in self.ignored:
                continue
            change_mute = None
            change_deaf = None
            if not self.game_running or self.is_meeting:
                if state.mute:
                    change_mute = False
                if state.deaf:
                    change_deaf = False
            else:
                if id in self.dead:
                    if state.mute:
                        change_mute = False
                    if state.deaf:
                        change_deaf = False
                else:
                    if not state.mute:
                        change_mute = True
                    if not state.deaf:
                        change_deaf = True

            if change_mute is not None or change_deaf is not None:
                kwargs = {}
                if change_mute is not None:
                    kwargs['mute'] = change_mute
                if change_deaf is not None:
                    kwargs['deafen'] = change_deaf
                if not (member := self.guild.get_member(id)):
                    member = await self.guild.fetch_member(id)
                await member.edit(
                    **kwargs,
                    reason='B0dge B0t sync'
                )
        print("Sync done.")

    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        if before.channel is None and after.channel.id == self.chan.id:
            # just joined channel
            await self.client.send_to_all({
                'kind': 'Channel/JOINED',
                'member': {
                    'id': member.id,
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
                    'id': member.id
                }
            })
        else:
            # state change
            pass


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
            web.get('/startGame', self.test_startGame)
        ])

        self.subs: Dict[str, GameChannel] = dict()
        self.socks: List[web.WebSocketResponse] = list()

    async def test_startGame(self, request: web.Request):
        sub = self.subs[os.getenv("DISCORD_MONITORED_CHANNEL_ID")]
        sub.game_running = True
        await sub.sync_state()
        return web.Response(body='')

    async def on_websocket(self, request: web.Request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        await ws.send_json({
            'kind': 'HELLO'
        })

        self.socks.append(ws)

        async for msg in ws:
            pass

    async def on_ready(self):
        print("Logged on as {}!".format(self.user))
        add_url = discord.utils.oauth_url(
            os.getenv("DISCORD_CLIENT_ID"),
            discord.Permissions(29361152),
            None,
            os.getenv("BASE_URL") + "/oauth/redirect"
        )
        print("Guilds:", self.guilds)
        for guild_ in self.guilds:
            guild: discord.Guild = guild_
            for chan in guild.voice_channels:
                print("Found chan {}".format(chan.id))
                if str(chan.id) == os.getenv("DISCORD_MONITORED_CHANNEL_ID"):
                    self.subs[str(chan.id)] = GameChannel(guild, chan, self)

        print("Add this bot to your server here: {}".format(add_url))

    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if after.channel is not None:
            if str(after.channel.id) in self.subs:
                await self.subs[str(after.channel.id)].on_voice_state_update(member, before, after)

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
