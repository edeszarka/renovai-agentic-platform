#!/bin/bash
# RenovAI Capstone Demo Script
# Run from the project root: bash demo/run_demo.sh
#
# Starts the MCP server, sends the two example queries from the spec's
# success criteria, and prints the responses.

set -e

echo "========================================================"
echo " RenovAI Capstone Demo"
echo " Agents for Good — Hungarian Renovation Due-Diligence"
echo "========================================================"

if [ ! -f .env ]; then
  echo "WARNING: .env file not found. Copy .env.example to .env first."
fi

echo ""
echo "Starting MCP server and sending test queries..."

uv run python -c "
import sys, json, subprocess, time, os
from pathlib import Path

class MCPClient:
    def __init__(self):
        self._req_id = 0
        self._proc = subprocess.Popen(
            [sys.executable, '-m', 'mcp_server.server'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1
        )
        time.sleep(0.5)
        self._send('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {}, 'clientInfo': {'name': 'demo', 'version': '1.0'}})

    def _send(self, method, params=None):
        self._req_id += 1
        req = {'jsonrpc': '2.0', 'id': self._req_id, 'method': method}
        if params is not None:
            req['params'] = params
        self._proc.stdin.write(json.dumps(req) + '\n')
        self._proc.stdin.flush()
        resp = json.loads(self._proc.stdout.readline())
        if 'error' in resp:
            raise RuntimeError(f\"MCP error: {resp['error']}\")
        return resp['result']

    def call_tool(self, name, args):
        result = self._send('tools/call', {'name': name, 'arguments': args})
        content = result.get('content', [])
        if content and content[0].get('type') == 'text':
            return json.loads(content[0]['text'])
        return result

    def close(self):
        self._proc.terminate()
        self._proc.wait(timeout=5)

client = MCPClient()

# Query 1a: Cost estimate
print('---')
print('QUERY 1: Cost estimate')
r = client.call_tool('estimate_renovation_cost', {'district': 8, 'area_sqm': 55, 'num_rooms': 2, 'scope': ['villany', 'viz_futes']})
if 'error' in r:
    print(f'ERROR: {r[\"error\"]}')
else:
    print(f'Low:  {r[\"low_huf\"]:>12,} Ft')
    print(f'Mid:  {r[\"mid_huf\"]:>12,} Ft')
    print(f'High: {r[\"high_huf\"]:>12,} Ft')
    print(f'Similar cases: {r.get(\"num_similar_cases\", 0)}')
    if r.get('warning'):
        print(f'Warning: {r[\"warning\"]}')

# Query 2: Due diligence
print('---')
print('QUERY 2: Due diligence advice')
r = client.call_tool('get_due_diligence_advice', {'district': 8, 'area_sqm': 55, 'building_type': 'tegla', 'condition': 'kozepes', 'known_issues': ['kohosalak']})
if 'error' in r:
    print(f'ERROR: {r[\"error\"]}')
else:
    print(f'Risk: {r.get(\"overall_risk\", \"N/A\")}')
    print(f'Seller questions: {len(r.get(\"questions_for_seller\", []))}')
    print(f'Checklist items: {len(r.get(\"inspection_checklist\", []))}')
    print(f'Red flags: {len(r.get(\"red_flags\", []))}')
    print(f'Sources: {len(r.get(\"sources_cited\", []))}')

client.close()
print('--- Demo complete ---')
" 2>&1
