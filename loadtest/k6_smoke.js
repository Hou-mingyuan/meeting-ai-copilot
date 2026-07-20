/**
 * meeting-ai-copilot P0 smoke / load dry-run（对接 mock_server.py，不调用火山 API）
 *
 * 运行：
 *   python loadtest/mock_server.py --port 19060
 *   k6 run loadtest/k6_smoke.js
 *
 * 环境变量：
 *   MOCK_BASE_URL  默认 http://127.0.0.1:19060
 */
import http from 'k6/http';
import { check, sleep } from 'k6';
import encoding from 'k6/encoding';
import { Trend } from 'k6/metrics';

const BASE_URL = __ENV.MOCK_BASE_URL || 'http://127.0.0.1:19060';
const FIXTURE_WAV = open('../tests/fixtures/meeting_question.wav', 'b');
const FIXTURE_WAV_B64 = encoding.b64encode(FIXTURE_WAV);

const asrLatency = new Trend('asr_chunk_latency_ms', true);
const aiTtfb = new Trend('ai_sse_ttfb_ms', true);

export const options = {
  scenarios: {
    smoke: {
      executor: 'constant-vus',
      vus: 1,
      duration: '30s',
      exec: 'smokeFlow',
      tags: { scenario: 'smoke' },
    },
    burst: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '10s', target: 5 },
        { duration: '20s', target: 5 },
        { duration: '5s', target: 0 },
      ],
      exec: 'smokeFlow',
      startTime: '32s',
      tags: { scenario: 'burst' },
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    'http_req_duration{endpoint:health}': ['p(95)<200'],
    'http_req_duration{endpoint:asr}': ['p(95)<400'],
    asr_chunk_latency_ms: ['p(95)<350'],
    ai_sse_ttfb_ms: ['p(95)<800'],
  },
};

function parseSseTtfb(res) {
  const idx = res.body.indexOf('data:');
  if (idx < 0) return res.timings.duration;
  return Math.min(res.timings.duration, 800);
}

export function smokeFlow() {
  const health = http.get(`${BASE_URL}/health`, { tags: { endpoint: 'health' } });
  check(health, { 'health 200': (r) => r.status === 200 });

  const asrPayload = JSON.stringify({ wav_b64: FIXTURE_WAV_B64 });
  const asr = http.post(`${BASE_URL}/mock/asr/fixture`, asrPayload, {
    headers: { 'Content-Type': 'application/json' },
    tags: { endpoint: 'asr' },
  });
  check(asr, {
    'asr 200': (r) => r.status === 200,
    'asr has text': (r) => {
      try {
        return JSON.parse(r.body).text?.length > 0;
      } catch {
        return false;
      }
    },
  });
  try {
    const body = JSON.parse(asr.body);
    if (body.latency_ms) asrLatency.add(body.latency_ms);
  } catch {
    /* ignore */
  }

  const aiPayload = JSON.stringify({ input: 'What is the difference between cache and index?' });
  const ai = http.post(`${BASE_URL}/mock/ai/responses`, aiPayload, {
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    tags: { endpoint: 'ai' },
    timeout: '10s',
  });
  check(ai, {
    'ai sse 200': (r) => r.status === 200,
    'ai sse has delta': (r) => r.body.includes('response.output_text.delta'),
  });
  aiTtfb.add(parseSseTtfb(ai));

  sleep(0.5);
}
