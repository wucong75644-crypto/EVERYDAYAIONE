"""Redis Lua scripts for the cross-worker confirmation state machine."""

CREATE_SCRIPT = r'''
local existing_identity = redis.call('GET', KEYS[1])
local existing = redis.call('HGETALL', KEYS[2])
if existing_identity then
  if existing_identity ~= ARGV[1] then return 'CREATE_CONFLICT' end
  if #existing == 0 then return 'MALFORMED_STATE' end
  local expected = {confirmation_id=ARGV[1], action_id=ARGV[2], interaction_id=ARGV[3], interaction_version=ARGV[4], task_id=ARGV[5], tool_call_id=ARGV[6], tool_name=ARGV[7], arguments_hash=ARGV[8], user_id=ARGV[9], org_id=ARGV[10], authorization_expires_at=ARGV[11], waiter_hash=ARGV[12]}
  if ARGV[13] ~= '' then expected.confirmation_group_hash=ARGV[13]; expected.confirmation_group_size=ARGV[14] end
  for field,value in pairs(expected) do
    if redis.call('HGET', KEYS[2], field) ~= value then return 'MALFORMED_STATE' end
  end
  local state = redis.call('HGET', KEYS[2], 'state')
  if not state then return 'MALFORMED_STATE' end
  return 'IDEMPOTENT:' .. state
end
if #existing ~= 0 then return 'CREATE_CONFLICT' end
local now = redis.call('TIME')
local created_ms = now[1] * 1000 + math.floor(now[2] / 1000)
local expires_ms = created_ms + tonumber(ARGV[15]) * 1000
redis.call('HSET', KEYS[2], 'confirmation_id',ARGV[1], 'action_id',ARGV[2], 'interaction_id',ARGV[3], 'interaction_version',ARGV[4], 'task_id',ARGV[5], 'tool_call_id',ARGV[6], 'tool_name',ARGV[7], 'arguments_hash',ARGV[8], 'user_id',ARGV[9], 'org_id',ARGV[10], 'authorization_expires_at',ARGV[11], 'waiter_hash',ARGV[12], 'created_at',created_ms, 'expires_at',expires_ms, 'state','PENDING')
if ARGV[13] ~= '' then redis.call('HSET',KEYS[2],'confirmation_group_hash',ARGV[13],'confirmation_group_size',ARGV[14]) end
redis.call('SET', KEYS[1], ARGV[1], 'EX', tonumber(ARGV[15]) + tonumber(ARGV[16]), 'NX')
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[15]) + tonumber(ARGV[16]))
return 'CREATED:PENDING'
'''

CONSUME_SCRIPT = r'''
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 'NOT_FOUND' end
if redis.call('HGET', KEYS[2], 'confirmation_id') ~= ARGV[1] then return 'MALFORMED_STATE' end
if redis.call('HGET', KEYS[2], 'user_id') ~= ARGV[2] or redis.call('HGET', KEYS[2], 'org_id') ~= ARGV[3] then return 'ACTOR_MISMATCH' end
if redis.call('HGET', KEYS[2], 'state') ~= 'PENDING' then return 'ALREADY_TERMINAL:' .. (redis.call('HGET', KEYS[2], 'state') or 'UNKNOWN') end
local now = redis.call('TIME'); local now_ms = now[1] * 1000 + math.floor(now[2] / 1000)
if now_ms >= tonumber(redis.call('HGET', KEYS[2], 'expires_at')) then redis.call('HSET',KEYS[2],'state','EXPIRED'); redis.call('EXPIRE',KEYS[2],tonumber(ARGV[5])); redis.call('EXPIRE',KEYS[1],tonumber(ARGV[5])); redis.call('RPUSH',KEYS[3],'EXPIRED'); redis.call('EXPIRE',KEYS[3],tonumber(ARGV[5])); return 'WON:EXPIRED' end
local state = ARGV[4] == '1' and 'APPROVED' or 'DENIED'
if state == 'APPROVED' then
  redis.call('HSET', KEYS[2], 'state', state, 'decided_at', now_ms, 'claim_expires_at', now_ms + tonumber(ARGV[6]) * 1000)
else
  redis.call('HSET', KEYS[2], 'state', state, 'decided_at', now_ms)
end
redis.call('EXPIRE',KEYS[2],tonumber(ARGV[5])); redis.call('EXPIRE',KEYS[1],tonumber(ARGV[5])); redis.call('RPUSH',KEYS[3],state); redis.call('EXPIRE',KEYS[3],tonumber(ARGV[5]))
return 'WON:' .. state
'''

EXPIRE_SCRIPT = r'''
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 'NOT_FOUND' end
local state = redis.call('HGET', KEYS[2], 'state')
if state ~= 'PENDING' and state ~= 'APPROVED' then return 'ALREADY_TERMINAL:' .. (state or 'UNKNOWN') end
local now = redis.call('TIME'); local now_ms = now[1] * 1000 + math.floor(now[2] / 1000)
local deadline = state == 'PENDING' and redis.call('HGET',KEYS[2],'expires_at') or redis.call('HGET',KEYS[2],'claim_expires_at')
if not deadline or now_ms < tonumber(deadline) then return 'NOT_DUE:' .. state end
redis.call('HSET',KEYS[2],'state','EXPIRED'); redis.call('EXPIRE',KEYS[2],tonumber(ARGV[2])); redis.call('EXPIRE',KEYS[1],tonumber(ARGV[2])); redis.call('RPUSH',KEYS[3],'EXPIRED'); redis.call('EXPIRE',KEYS[3],tonumber(ARGV[2])); return 'WON:EXPIRED'
'''

CLAIM_SCRIPT = r'''
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 'NOT_FOUND' end
if redis.call('HGET',KEYS[2],'waiter_hash') ~= ARGV[2] then return 'WAITER_MISMATCH' end
local state = redis.call('HGET',KEYS[2],'state')
if state ~= 'APPROVED' then return 'NOT_APPROVED:' .. (state or 'UNKNOWN') end
local now = redis.call('TIME'); local now_ms = now[1] * 1000 + math.floor(now[2] / 1000)
local deadline = redis.call('HGET',KEYS[2],'claim_expires_at')
if not deadline or now_ms >= tonumber(deadline) then redis.call('HSET',KEYS[2],'state','EXPIRED'); redis.call('EXPIRE',KEYS[2],tonumber(ARGV[3])); redis.call('EXPIRE',KEYS[1],tonumber(ARGV[3])); return 'WON:EXPIRED' end
redis.call('HSET',KEYS[2],'state','EXECUTION_CLAIMED','claimed_at',now_ms); redis.call('EXPIRE',KEYS[2],tonumber(ARGV[3])); redis.call('EXPIRE',KEYS[1],tonumber(ARGV[3])); return 'WON:EXECUTION_CLAIMED'
'''

READ_SCRIPT = r'''
if redis.call('GET',KEYS[1]) ~= ARGV[1] then return {'NOT_FOUND'} end
local values = redis.call('HGETALL',KEYS[2]); if #values == 0 then return {'MALFORMED_STATE'} end; return values
'''
