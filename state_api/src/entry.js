import worker from './index.js';

function jsonResponse(value, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
    },
  });
}

function constantTimeEqual(left, right) {
  const a = new TextEncoder().encode(String(left || ''));
  const b = new TextEncoder().encode(String(right || ''));
  if (a.length !== b.length) return false;
  let result = 0;
  for (let index = 0; index < a.length; index += 1) result |= a[index] ^ b[index];
  return result === 0;
}

function internalAuthorized(request, env) {
  const header = request.headers.get('Authorization') || '';
  const token = header.startsWith('Bearer ') ? header.slice(7) : '';
  const expected = String(env.STATE_API_TOKEN || env.BOT_TOKEN || '');
  return Boolean(expected && token && constantTimeEqual(token, expected));
}

function parseStored(value) {
  try {
    return JSON.parse(String(value));
  } catch {
    return value;
  }
}

function normalizeStoredSettings(payload) {
  if (!payload || typeof payload !== 'object') return payload;
  if (payload.state?.settings && typeof payload.state.settings === 'object') {
    for (const [key, value] of Object.entries(payload.state.settings)) {
      payload.state.settings[key] = parseStored(value);
    }
  }
  if (payload.settings && typeof payload.settings === 'object') {
    for (const [key, value] of Object.entries(payload.settings)) {
      payload.settings[key] = parseStored(value);
    }
  }
  return payload;
}

async function normalizedResponse(response) {
  const contentType = response.headers.get('Content-Type') || '';
  if (!contentType.includes('application/json')) return response;
  const payload = normalizeStoredSettings(await response.json());
  const headers = new Headers(response.headers);
  headers.set('Cache-Control', 'no-store');
  return new Response(JSON.stringify(payload), {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

async function readBody(request) {
  try {
    const value = await request.json();
    return value && typeof value === 'object' ? value : {};
  } catch {
    return {};
  }
}

async function upsertAccess(request, env) {
  if (!internalAuthorized(request, env)) return jsonResponse({ ok: false, error: 'Unauthorized' }, 401);
  const access = await readBody(request);
  const users = access.users && typeof access.users === 'object' ? access.users : {};
  const ownerId = String(access.owner_id || '');
  const admins = new Set((access.admins || []).map(String));
  const blocked = new Set((access.blocked_users || []).map(String));
  const current = new Date().toISOString();
  const statements = [];

  for (const [idRaw, recordRaw] of Object.entries(users)) {
    const id = String(idRaw);
    const record = recordRaw && typeof recordRaw === 'object' ? recordRaw : {};
    statements.push(env.DB.prepare(`
      INSERT INTO users (id,chat_id,username,first_name,last_name,photo_url,first_seen_at,last_seen_at,blocked)
      VALUES (?,?,?,?,?,?,?,?,?)
      ON CONFLICT(id) DO UPDATE SET
        chat_id=excluded.chat_id,
        username=excluded.username,
        first_name=excluded.first_name,
        last_name=excluded.last_name,
        photo_url=excluded.photo_url,
        first_seen_at=CASE WHEN users.first_seen_at='' THEN excluded.first_seen_at ELSE users.first_seen_at END,
        last_seen_at=excluded.last_seen_at,
        blocked=excluded.blocked
    `).bind(
      id,
      String(record.chat_id || id),
      String(record.username || ''),
      String(record.first_name || ''),
      String(record.last_name || ''),
      String(record.photo_url || ''),
      String(record.first_seen_at || current),
      String(record.last_seen_at || current),
      blocked.has(id) ? 1 : 0,
    ));
    const role = id === ownerId ? 'owner' : (admins.has(id) ? 'admin' : 'user');
    statements.push(env.DB.prepare(`
      INSERT INTO roles (user_id,role) VALUES (?,?)
      ON CONFLICT(user_id) DO UPDATE SET role=excluded.role
    `).bind(id, role));
    statements.push(env.DB.prepare('DELETE FROM notification_preferences WHERE user_id=?').bind(id));
    statements.push(env.DB.prepare('DELETE FROM wheel_participation WHERE user_id=?').bind(id));
    statements.push(env.DB.prepare('DELETE FROM hidden_wheels WHERE user_id=?').bind(id));

    const preferences = record.notification_preferences && typeof record.notification_preferences === 'object'
      ? record.notification_preferences
      : { wheels: record.notifications_enabled !== false };
    for (const [key, enabled] of Object.entries(preferences)) {
      statements.push(env.DB.prepare(`
        INSERT INTO notification_preferences (user_id,preference_key,enabled) VALUES (?,?,?)
      `).bind(id, key, enabled ? 1 : 0));
    }
    const participating = record.participating_wheels && typeof record.participating_wheels === 'object'
      ? record.participating_wheels : {};
    for (const [key, raw] of Object.entries(participating)) {
      const entry = raw && typeof raw === 'object' ? raw : {};
      statements.push(env.DB.prepare(`
        INSERT INTO wheel_participation (user_id,wheel_key,joined_at,active) VALUES (?,?,?,1)
      `).bind(id, String(key).toLowerCase(), String(entry.joined_at || current)));
    }
    const hidden = record.hidden_wheels && typeof record.hidden_wheels === 'object'
      ? record.hidden_wheels : {};
    for (const [key, raw] of Object.entries(hidden)) {
      const entry = raw && typeof raw === 'object' ? raw : {};
      statements.push(env.DB.prepare(`
        INSERT INTO hidden_wheels (user_id,wheel_key,hidden_at,expires_at,active) VALUES (?,?,?,?,1)
      `).bind(
        id,
        String(key).toLowerCase(),
        String(entry.hidden_at || current),
        entry.expires_at || null,
      ));
    }
  }

  const settings = access.settings && typeof access.settings === 'object' ? access.settings : {};
  for (const [key, value] of Object.entries(settings)) {
    statements.push(env.DB.prepare(`
      INSERT INTO system_settings (setting_key,setting_value) VALUES (?,?)
      ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value
    `).bind(key, JSON.stringify(value)));
  }
  if (statements.length) await env.DB.batch(statements);
  return jsonResponse({ ok: true });
}

async function upsertSourceRequests(request, env) {
  if (!internalAuthorized(request, env)) return jsonResponse({ ok: false, error: 'Unauthorized' }, 401);
  const payload = await readBody(request);
  const requests = payload.requests && typeof payload.requests === 'object' ? payload.requests : {};
  const statements = [];
  for (const [idRaw, raw] of Object.entries(requests)) {
    const value = raw && typeof raw === 'object' ? raw : {};
    const id = String(value.id || idRaw);
    const requesterId = String(value.requester_id || 'unknown');
    const createdAt = String(value.created_at || new Date().toISOString());
    statements.push(env.DB.prepare(`
      INSERT INTO users (id,chat_id,username,first_name,last_name,photo_url,first_seen_at,last_seen_at,blocked)
      VALUES (?,?,?,?,?,?,?,?,0) ON CONFLICT(id) DO NOTHING
    `).bind(
      requesterId,
      String(value.requester_chat_id || requesterId),
      String(value.requester_username || ''),
      '', '', '', createdAt, createdAt,
    ));
    statements.push(env.DB.prepare(`
      INSERT INTO roles (user_id,role) VALUES (?,'user') ON CONFLICT(user_id) DO NOTHING
    `).bind(requesterId));
    statements.push(env.DB.prepare(`
      INSERT INTO source_requests
        (id,source,status,created_at,requester_id,requester_chat_id,requester_name,
         requester_username,check_json,destination,decision_text,decided_at,decided_by)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(id) DO UPDATE SET
        source=excluded.source,
        status=excluded.status,
        requester_id=excluded.requester_id,
        requester_chat_id=excluded.requester_chat_id,
        requester_name=excluded.requester_name,
        requester_username=excluded.requester_username,
        check_json=excluded.check_json,
        destination=excluded.destination,
        decision_text=excluded.decision_text,
        decided_at=excluded.decided_at,
        decided_by=excluded.decided_by
    `).bind(
      id,
      String(value.source || ''),
      String(value.status || 'pending'),
      createdAt,
      requesterId,
      String(value.requester_chat_id || requesterId),
      String(value.requester_name || ''),
      String(value.requester_username || ''),
      JSON.stringify(value.check || {}),
      String(value.destination || ''),
      String(value.decision_text || ''),
      value.decided_at || null,
      value.decided_by || null,
    ));
  }
  if (statements.length) await env.DB.batch(statements);
  return jsonResponse({ ok: true });
}

export default {
  async fetch(request, env, context) {
    const url = new URL(request.url);
    if (url.pathname === '/v1/admin/access' && request.method === 'PUT') {
      return upsertAccess(request, env);
    }
    if (url.pathname === '/v1/admin/source-requests' && request.method === 'PUT') {
      return upsertSourceRequests(request, env);
    }
    const response = await worker.fetch(request, env, context);
    if (
      (url.pathname === '/v1/session' && request.method === 'POST') ||
      (url.pathname === '/v1/admin/access' && request.method === 'GET')
    ) {
      return normalizedResponse(response);
    }
    return response;
  },
};
