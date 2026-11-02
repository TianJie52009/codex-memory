import json
from pathlib import Path
import sys
import tempfile
import unittest
from mcp import Client, StdioServerParameters


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdio_tools_and_fresh_process_persistence(self):
        with tempfile.TemporaryDirectory(prefix='codex-memory-mcp-') as directory:
            params = StdioServerParameters(command=sys.executable, args=['-X', 'utf8', '-u', str(Path(__file__).with_name('memory.py')), '--root', directory, 'serve'])
            async with Client(params) as client:
                tools = await client.list_tools()
                self.assertIn('memory', [t.name for t in tools.tools])
                result = await client.call_tool('memory', {'action': 'save', 'data': {'key': 'stdio测试', 'title': '本机MCP有效', 'body': '只有测试环境会保存这条内容', 'scope': 'global', 'kind': 'workflow', 'evidence': {'kind': 'verified', 'origin': 'local-mcp-test', 'text': '真实stdio客户端完成握手和调用'}}})
                saved = result.structured_content
                self.assertTrue(saved['saved'])
            async with Client(params) as client:
                recalled = (await client.call_tool('memory', {'action': 'search', 'data': {'query': 'stdio测试'}})).structured_content
                self.assertEqual(recalled['items'][0]['id'], saved['id'])
                bad = (await client.call_tool('memory', {'action': 'read', 'data': {'id': '../outside'}})).structured_content
                self.assertFalse(bad['ok'])
                gone = (await client.call_tool('memory', {'action': 'forget', 'data': {'id': saved['id'], 'expected_revision': 1}})).structured_content
                self.assertEqual(gone['forgotten'], saved['id'])
                self.assertEqual((await client.call_tool('memory', {'action': 'list'})).structured_content['items'], [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
