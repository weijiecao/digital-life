import asyncio
import tempfile

import sim_astrbot as sim


async def main():
    root = tempfile.mkdtemp(prefix='dbg-metrics4-')
    cfg = {'enabled': True, 'allowed_sender_ids': '42', 'automatic_memory_enabled': True}
    runtime = sim.SimRuntime(root, cfg)
    await runtime.start()
    try:
        event = await runtime.receive('我喜欢吃苹果', message_id='m0',
                                      origin='webchat:FriendMessage:42')
        await runtime.llm_request(event)
        plugin = runtime.plugin
        event = await runtime.receive('喜欢吃苹果吗', message_id='m1',
                                      origin='webchat:FriendMessage:42')
        identity = plugin._identity(event)
        scope = plugin.store.scope(*identity)
        loop = asyncio.get_running_loop()

        def probe():
            store = plugin.store
            rows = store.flow.db.execute(
                'SELECT seq,state,content FROM dl_capture WHERE scope=?', (scope,)).fetchall()
            pairs = store._character_pairs('喜欢吃苹果吗')
            candidates = store.pending_index.candidates(scope, pairs)
            pending = store._pending_context(scope, '喜欢吃苹果吗',
                                             '2099-01-01T00:00:00+00:00', 450, 'm1', set())
            return [dict(row) for row in rows], sorted(pairs), candidates, len(pending)

        rows, pairs, candidates, pending_len = await loop.run_in_executor(plugin.pool, probe)
        print('rows', rows)
        print('pairs', pairs)
        print('candidates', candidates)
        print('pending_len', pending_len)
    finally:
        await runtime.stop()


asyncio.run(main())
