"""CDP client via chrome-devtools-mcp (attach to user's open Chrome)."""
import json
import re
import subprocess
import sys
import time

MCP_CMD = [
    'cmd', '/c', r'C:\Users\Administrator\AppData\Roaming\npm\chrome-devtools-mcp.cmd',
    '--autoConnect', '--experimentalPageIdRouting',
]


class CDPClient:
    def __init__(self):
        self.proc = subprocess.Popen(
            MCP_CMD, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding='utf-8', bufsize=1)
        self._id = 0

    def _rpc(self, method, params=None):
        self._id += 1
        msg = json.dumps({"jsonrpc": "2.0", "id": self._id,
                          "method": method, "params": params or {}})
        self.proc.stdin.write(msg + "\n")
        self.proc.stdin.flush()
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                continue
            try:
                data = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                # 事件/日志行，跳过
                if "server-started" in line:
                    continue
                continue
            if data.get("id") == self._id:
                if data.get("error"):
                    raise RuntimeError(f"MCP error: {data['error']}")
                return data.get("result", {})
        raise TimeoutError("MCP RPC timeout")

    def call(self, method, params=None):
        """Raw MCP tool call via tools/call."""
        return self._rpc("tools/call", {"name": method, "arguments": params or {}})

    def eval_json(self, page_id, js_func, timeout=60):
        """Evaluate a JS function on a page; returns parsed JSON result."""
        resp = self._rpc("tools/call", {
            "name": "evaluate_script",
            "arguments": {"function": js_func, "pageId": page_id,
                          "awaitPromise": True},
        })
        text = resp.get("content", [{}])[0].get("text", "")
        # MCP returns "Script ran on page and returned:\n```json\n...\n```"
        m = re.search(r"```json\s*\r?\n(.*?)\r?\n```", text, re.S)
        if m:
            return json.loads(m.group(1))
        m2 = re.search(r"```json\s*(.*?)```", text, re.S)
        if m2:
            return json.loads(m2.group(1))
        return text

    def navigate(self, page_id, url, timeout=90):
        self._rpc("navigate_page", {"pageId": page_id, "url": url})

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    cdp = CDPClient()
    try:
        pages = cdp.call("list_pages")
        print(pages["result"]["content"][0]["text"])
    finally:
        cdp.close()
