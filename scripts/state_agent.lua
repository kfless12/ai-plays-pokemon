-- state_agent.lua  v4
-- Passive: writes state JSON to disk; reads action commands from disk and applies them.
-- Does not call emu:runFrame().
--
-- v4 fixes:
--   1. Play time addresses corrected (hours=0xDA41, minutes=0xDA43)
--   2. Textbox detection uses tile map scanning instead of stale wTextBoxID
--   3. Warp addresses corrected (count=0xD3AE, table=0xD3AF), duplicates filtered
--   4. Menu detection uses wd730 bit 5 + heuristics
--   5. Textbox text reading from tile map with Pokemon Red character decoding
--   6. Sign data structure corrected (coords and text IDs are separate arrays)

local UPDATE_EVERY_FRAMES = 10
local ACTION_POLL_FRAMES  = 2

local STATE_PATH  = "/home/kfless/pokemon_ai/state.json"
local ACTION_PATH = "/home/kfless/pokemon_ai/action.txt"
local LOG_PATH    = "/home/kfless/pokemon_ai/event_logs.txt"

local function log_event(msg)
  local f = io.open(LOG_PATH, "a")
  if f then
    f:write(string.format("[%d] %s\n", emu:currentFrame(), msg))
    f:close()
  end
end

-- ===== Addresses (Pokemon Red) =====
local ADDR_MAP        = 0xD35E
local ADDR_X          = 0xD362
local ADDR_Y          = 0xD361
local ADDR_BATTLETYPE = 0xD057
local ADDR_FACING     = 0xC109
local ADDR_MONEY      = 0xD347
local ADDR_BADGES     = 0xD356

-- Play time (corrected)
local ADDR_HOURS      = 0xDA41
local ADDR_MINUTES    = 0xDA43
local ADDR_SECONDS    = 0xDA44

-- Bag
local ADDR_BAG_COUNT  = 0xD31D
local ADDR_BAG_ITEMS  = 0xD31E
local MAX_BAG_ITEMS   = 20

-- Party
local ADDR_PARTY_COUNT   = 0xD163
local ADDR_PARTY_SPECIES = 0xD164
local PARTY_BASE   = 0xD16B
local PARTY_STRIDE = 0x2C
local OFF_CUR_HP   = 0x01
local OFF_STATUS   = 0x04
local OFF_MOVE1    = 0x08
local OFF_PP1      = 0x1D
local OFF_LEVEL    = 0x21
local OFF_MAX_HP   = 0x22

-- Enemy
local ADDR_ENEMY_SPECIES = 0xCFE5
local ADDR_ENEMY_HP      = 0xCFE6
local ADDR_ENEMY_LEVEL_A = 0xCFE8
local ADDR_ENEMY_LEVEL_B = 0xCFF3
local ADDR_ENEMY_MAXHP   = 0xCFF4
local ADDR_ENEMY_STATUS  = 0xCFE9

-- Sprites
local SPRITE_TABLE_BASE = 0xC100
local SPRITE_ENTRY_SIZE = 0x10
local MAX_SPRITES       = 16

-- Warps (corrected)
local ADDR_NUM_WARPS  = 0xD3AE
local ADDR_WARP_TABLE = 0xD3AF
local WARP_ENTRY_SIZE = 4
local MAX_WARPS       = 32

-- Signs (corrected structure)
local ADDR_NUM_SIGNS     = 0xD4B0
local ADDR_SIGN_COORDS   = 0xD4B1
local ADDR_SIGN_TEXT_IDS = 0xD4D1
local MAX_SIGNS          = 16

-- UI
local ADDR_MENU_ITEM     = 0xCC26
local ADDR_MENU_CURSOR_Y = 0xCC24
local ADDR_MENU_CURSOR_X = 0xCC25
local ADDR_MENU_MAX_ITEM = 0xCC28
local ADDR_MOVE_LIST_IDX = 0xCC2E
local ADDR_TEXTBOX_ID    = 0xD125
local ADDR_GAME_STATUS   = 0xD72E
local ADDR_ANIM_COUNTER  = 0xCFCB
local ADDR_SPRITE_FLAGS  = 0xC104
local ADDR_WD730         = 0xD730

local BATTLE_MENU_COL_RIGHT = 0x0F

-- Tile map
local ADDR_TILEMAP   = 0xC3A0
local TILEMAP_WIDTH  = 20
local TILEMAP_HEIGHT = 18

-- ===== Memory helpers =====
local function u8(a) return emu:read8(a) end
local function u16le(a) return u8(a) + u8(a+1) * 256 end
local function bit_set(val, bit_n)
  return (math.floor(val / (2 ^ bit_n)) % 2) == 1
end

-- ===== Pokemon Red Character Encoding =====
local CHAR_MAP = {}
for i = 0, 25 do CHAR_MAP[0x80 + i] = string.char(65 + i) end  -- A-Z
for i = 0, 25 do CHAR_MAP[0xA0 + i] = string.char(97 + i) end  -- a-z
for i = 0, 9  do CHAR_MAP[0xF6 + i] = string.char(48 + i) end  -- 0-9
CHAR_MAP[0x7F] = " "
CHAR_MAP[0xE0] = "'"
CHAR_MAP[0xE3] = "-"
CHAR_MAP[0xE6] = "?"
CHAR_MAP[0xE7] = "!"
CHAR_MAP[0xE8] = "."
CHAR_MAP[0xF0] = "$"
CHAR_MAP[0xF4] = ","
CHAR_MAP[0xF3] = "/"
CHAR_MAP[0x50] = ""
CHAR_MAP[0x4E] = " "
CHAR_MAP[0x4F] = " "
CHAR_MAP[0x51] = " "
CHAR_MAP[0x55] = " "

local function is_text_tile(tid) return CHAR_MAP[tid] ~= nil end
local function tile_to_char(tid) return CHAR_MAP[tid] or "" end

-- ===== Tile Map Text Reader =====
local function read_tilemap_text()
  local text_lines = {}
  local has_any = false
  for row = 0, TILEMAP_HEIGHT - 1 do
    local line = ""
    local text_count = 0
    for col = 0, TILEMAP_WIDTH - 1 do
      local tile = u8(ADDR_TILEMAP + row * TILEMAP_WIDTH + col)
      if is_text_tile(tile) then
        local ch = tile_to_char(tile)
        line = line .. ch
        if ch ~= " " and ch ~= "" then text_count = text_count + 1 end
      else
        if #line > 0 and line:sub(-1) ~= " " then line = line .. " " end
      end
    end
    line = line:match("^%s*(.-)%s*$") or ""
    if text_count >= 3 and #line > 0 then
      text_lines[#text_lines + 1] = line
      has_any = true
    end
  end
  if not has_any then return false, "" end
  local full = table.concat(text_lines, " "):gsub("%s+", " ")
  full = full:match("^%s*(.-)%s*$") or ""
  return true, full
end

local function detect_textbox_from_tilemap()
  local text_count = 0
  local border_count = 0
  for row = 10, TILEMAP_HEIGHT - 1 do
    for col = 0, TILEMAP_WIDTH - 1 do
      local tile = u8(ADDR_TILEMAP + row * TILEMAP_WIDTH + col)
      if is_text_tile(tile) then
        local ch = tile_to_char(tile)
        if ch ~= " " and ch ~= "" then text_count = text_count + 1 end
      end
      if tile == 0x79 or tile == 0x7A or tile == 0x7B or tile == 0x7C
         or tile == 0x7E or tile == 0x7D then
        border_count = border_count + 1
      end
    end
  end
  return (border_count >= 4 and text_count >= 5)
end

-- ===== Player info =====
local FACING_NAMES = { [0]="down", [4]="up", [8]="left", [0x0C]="right" }

local function read_facing()
  return FACING_NAMES[u8(ADDR_FACING)] or "unknown"
end

local function read_money()
  local function bcd(b) return math.floor(b/16)*10 + (b%16) end
  return bcd(u8(ADDR_MONEY))*10000 + bcd(u8(ADDR_MONEY+1))*100 + bcd(u8(ADDR_MONEY+2))
end

local function read_badges()
  local raw = u8(ADDR_BADGES)
  local count = 0
  for i = 0, 7 do
    if math.floor(raw / (2^i)) % 2 == 1 then count = count + 1 end
  end
  return count, raw
end

local function decode_status(sb)
  if sb == 0 then return "OK" end
  if sb % 8 > 0 then return "SLP" end
  if math.floor(sb/8)%2 == 1 then return "PSN" end
  if math.floor(sb/16)%2 == 1 then return "BRN" end
  if math.floor(sb/32)%2 == 1 then return "FRZ" end
  if math.floor(sb/64)%2 == 1 then return "PAR" end
  return "OK"
end

-- ===== Party =====
local function read_party_slot(slot)
  local base = PARTY_BASE + ((slot-1) * PARTY_STRIDE)
  local moves, pps = {}, {}
  for k = 0, 3 do
    moves[k+1] = u8(base + OFF_MOVE1 + k)
    pps[k+1]   = u8(base + OFF_PP1 + k)
  end
  return {
    species = u8(ADDR_PARTY_SPECIES + (slot-1)),
    lvl = u8(base + OFF_LEVEL),
    hp = u16le(base + OFF_CUR_HP),
    maxhp = u16le(base + OFF_MAX_HP),
    status = decode_status(u8(base + OFF_STATUS)),
    moves = moves, pps = pps,
  }
end

local function read_enemy()
  local lvlA = u8(ADDR_ENEMY_LEVEL_A)
  local lvlB = u8(ADDR_ENEMY_LEVEL_B)
  return {
    species = u8(ADDR_ENEMY_SPECIES),
    lvl = (lvlA ~= 0) and lvlA or lvlB,
    hp = u16le(ADDR_ENEMY_HP),
    maxhp = u16le(ADDR_ENEMY_MAXHP),
    status = decode_status(u8(ADDR_ENEMY_STATUS)),
  }
end

-- ===== Bag =====
local function read_bag()
  local count = u8(ADDR_BAG_COUNT)
  if count > MAX_BAG_ITEMS then count = MAX_BAG_ITEMS end
  local items = {}
  for i = 0, count-1 do
    local addr = ADDR_BAG_ITEMS + (i*2)
    local id = u8(addr)
    if id ~= 0 and id ~= 0xFF then
      items[#items+1] = { id = id, qty = u8(addr+1) }
    end
  end
  return items
end

-- ===== Sprites =====
local function read_nearby_sprites()
  local sprites = {}
  for i = 1, MAX_SPRITES-1 do
    local base = SPRITE_TABLE_BASE + (i * SPRITE_ENTRY_SIZE)
    local pid = u8(base)
    if pid ~= 0 then
      sprites[#sprites+1] = {
        sprite_id = i, picture_id = pid,
        screen_y = u8(base+3), screen_x = u8(base+5),
        facing = FACING_NAMES[u8(base+9)] or "unknown",
      }
    end
  end
  return sprites
end

-- ===== Warps (corrected) =====
local function read_warps()
  local count = u8(ADDR_NUM_WARPS)
  if count > MAX_WARPS then count = MAX_WARPS end
  local warps, seen = {}, {}
  for i = 0, count-1 do
    local addr = ADDR_WARP_TABLE + (i * WARP_ENTRY_SIZE)
    local wy, wx = u8(addr), u8(addr+1)
    local dw, dm = u8(addr+2), u8(addr+3)
    local key = wy.."_"..wx.."_"..dw.."_"..dm
    if not seen[key] then
      seen[key] = true
      warps[#warps+1] = { y=wy, x=wx, dest_warp=dw, dest_map=dm }
    end
  end
  return warps
end

-- ===== Signs (corrected) =====
local function read_signs()
  local count = u8(ADDR_NUM_SIGNS)
  if count > MAX_SIGNS then count = MAX_SIGNS end
  local signs = {}
  for i = 0, count-1 do
    local ca = ADDR_SIGN_COORDS + (i*2)
    signs[#signs+1] = {
      y = u8(ca), x = u8(ca+1),
      text_id = u8(ADDR_SIGN_TEXT_IDS + i),
    }
  end
  return signs
end

-- ===== UI State =====
local function read_ui()
  local bt = u8(ADDR_BATTLETYPE)
  local wd730 = u8(ADDR_WD730)
  local anim = u8(ADDR_ANIM_COUNTER)
  local textbox_id = u8(ADDR_TEXTBOX_ID)
  local party_count = u8(ADDR_PARTY_COUNT)
  local map_id = u8(ADDR_MAP)
  local menu_item = u8(ADDR_MENU_ITEM)
  local menu_cursor_y = u8(ADDR_MENU_CURSOR_Y)
  local menu_cursor_x = u8(ADDR_MENU_CURSOR_X)
  local menu_max = u8(ADDR_MENU_MAX_ITEM)
  local move_list_idx = u8(ADDR_MOVE_LIST_IDX)

  local in_battle = (bt ~= 0)
  local joypad_disabled = bit_set(wd730, 5)
  local text_printing = (anim == 0xFF)

  -- Textbox: tile map scanning
  local textbox_active = detect_textbox_from_tilemap()
  local has_screen_text, screen_text = read_tilemap_text()

  -- Menu: wd730 bit 5 + menu_max > 0 + not a textbox
  local menu_active = joypad_disabled and (menu_max > 0) and (not textbox_active)
  if in_battle and menu_max > 0 then menu_active = true end

  local battle_menu_selection = "NONE"
  if in_battle and menu_active then
    local is_right = (menu_cursor_x >= BATTLE_MENU_COL_RIGHT)
    if menu_item == 0 then
      battle_menu_selection = is_right and "PKMN" or "FIGHT"
    else
      battle_menu_selection = is_right and "RUN" or "ITEM"
    end
  end

  local startup_phase = "UNKNOWN"
  if party_count > 0 or u8(ADDR_SPRITE_FLAGS) ~= 0 then
    startup_phase = "PLAYING"
  elseif map_id == 0 and (not textbox_active) and (not in_battle) then
    startup_phase = "TITLE_SCREEN"
  else
    startup_phase = "INTRO_SCRIPT"
  end

  return {
    in_battle = in_battle, battle_type = bt,
    textbox_active = textbox_active, textbox_id = textbox_id,
    text_printing = text_printing,
    screen_text = screen_text, has_screen_text = has_screen_text,
    menu_active = menu_active, joypad_disabled = joypad_disabled,
    wd730 = wd730,
    current_menu_item = menu_item,
    menu_cursor_x = menu_cursor_x, menu_cursor_y = menu_cursor_y,
    menu_max_item = menu_max,
    battle_menu_selection = battle_menu_selection,
    move_list_index = move_list_idx,
    startup_phase = startup_phase,
    party_count = party_count, anim_counter = anim,
  }
end

-- ===== JSON helpers =====
local function json_escape(s)
  s = s:gsub("\\", "\\\\")
  s = s:gsub("\"", "\\\"")
  s = s:gsub("\n", "\\n")
  s = s:gsub("\r", "\\r")
  s = s:gsub("\t", "\\t")
  s = s:gsub("[%c]", "")
  return s
end

local function bool_str(b) return b and "true" or "false" end

-- ===== State writer =====
local last_ui_phase = nil
last_action_str = ""

local function write_state()
  local bt = u8(ADDR_BATTLETYPE)
  local ui = read_ui()
  local px, py = u8(ADDR_X), u8(ADDR_Y)

  if ui.startup_phase ~= last_ui_phase then
    log_event(string.format("PHASE_CHANGE %s -> %s", tostring(last_ui_phase), ui.startup_phase))
    last_ui_phase = ui.startup_phase
  end

  local pc = ui.party_count
  local enemy = (bt ~= 0) and read_enemy() or nil
  local facing = read_facing()
  local money = read_money()
  local bc, bb = read_badges()
  local hrs = u8(ADDR_HOURS)
  local mins = u8(ADDR_MINUTES)
  local secs = u8(ADDR_SECONDS)

  local p = {}
  local function a(l) p[#p+1] = l end

  a("{")
  a(string.format("\"frame\":%d,", emu:currentFrame()))
  a(string.format("\"map\":%d,\"x\":%d,\"y\":%d,", u8(ADDR_MAP), px, py))
  a(string.format("\"facing\":\"%s\",", facing))
  a(string.format("\"money\":%d,\"badges\":%d,\"badge_bits\":%d,", money, bc, bb))
  a(string.format("\"play_time\":\"%d:%02d\",\"play_hours\":%d,\"play_minutes\":%d,\"play_seconds\":%d,", hrs, mins, hrs, mins, secs))
  a(string.format("\"battleType\":%d,", bt))

  -- UI
  a("\"ui\":{")
  a(string.format("\"in_battle\":%s,\"battle_type\":%d,", bool_str(ui.in_battle), ui.battle_type))
  a(string.format("\"textbox_active\":%s,\"textbox_id\":%d,", bool_str(ui.textbox_active), ui.textbox_id))
  a(string.format("\"text_printing\":%s,", bool_str(ui.text_printing)))
  if ui.has_screen_text and ui.screen_text ~= "" then
    a(string.format("\"screen_text\":\"%s\",", json_escape(ui.screen_text)))
  else
    a("\"screen_text\":\"\",")
  end
  a(string.format("\"menu_active\":%s,\"joypad_disabled\":%s,\"wd730\":%d,", bool_str(ui.menu_active), bool_str(ui.joypad_disabled), ui.wd730))
  a(string.format("\"current_menu_item\":%d,\"menu_cursor_x\":%d,\"menu_cursor_y\":%d,\"menu_max_item\":%d,", ui.current_menu_item, ui.menu_cursor_x, ui.menu_cursor_y, ui.menu_max_item))
  a(string.format("\"battle_menu_selection\":\"%s\",\"move_list_index\":%d,", ui.battle_menu_selection, ui.move_list_index))
  a(string.format("\"startup_phase\":\"%s\",\"party_count\":%d,\"anim_counter\":%d", ui.startup_phase, ui.party_count, ui.anim_counter))
  a("},")

  -- Party
  a("\"party\":[")
  for slot = 1, pc do
    local m = read_party_slot(slot)
    if slot > 1 then a(",") end
    a(string.format("{\"species\":%d,\"lvl\":%d,\"hp\":%d,\"maxhp\":%d,\"status\":\"%s\",\"moves\":[%d,%d,%d,%d],\"pps\":[%d,%d,%d,%d]}",
      m.species, m.lvl, m.hp, m.maxhp, m.status,
      m.moves[1], m.moves[2], m.moves[3], m.moves[4],
      m.pps[1], m.pps[2], m.pps[3], m.pps[4]))
  end
  a("],")

  -- slot1 compat
  if pc > 0 then
    local s = read_party_slot(1)
    a(string.format("\"slot1\":{\"lvl\":%d,\"hp\":%d,\"maxhp\":%d,\"moves\":[%d,%d,%d,%d],\"pps\":[%d,%d,%d,%d]},",
      s.lvl, s.hp, s.maxhp, s.moves[1], s.moves[2], s.moves[3], s.moves[4], s.pps[1], s.pps[2], s.pps[3], s.pps[4]))
  else
    a("\"slot1\":{\"lvl\":0,\"hp\":0,\"maxhp\":0,\"moves\":[0,0,0,0],\"pps\":[0,0,0,0]},")
  end

  -- Enemy
  if enemy then
    a(string.format("\"enemy\":{\"species\":%d,\"lvl\":%d,\"hp\":%d,\"maxhp\":%d,\"status\":\"%s\"},",
      enemy.species, enemy.lvl, enemy.hp, enemy.maxhp, enemy.status))
  else
    a("\"enemy\":null,")
  end

  -- Bag
  local bag = read_bag()
  a("\"bag\":[")
  for i, item in ipairs(bag) do
    if i > 1 then a(",") end
    a(string.format("{\"id\":%d,\"qty\":%d}", item.id, item.qty))
  end
  a("],")

  -- Sprites
  local sprites = read_nearby_sprites()
  a("\"sprites\":[")
  for i, sp in ipairs(sprites) do
    if i > 1 then a(",") end
    a(string.format("{\"sprite_id\":%d,\"picture_id\":%d,\"screen_y\":%d,\"screen_x\":%d,\"facing\":\"%s\"}",
      sp.sprite_id, sp.picture_id, sp.screen_y, sp.screen_x, sp.facing))
  end
  a("],")

  -- Warps
  local warps = read_warps()
  a("\"warps\":[")
  for i, w in ipairs(warps) do
    if i > 1 then a(",") end
    a(string.format("{\"y\":%d,\"x\":%d,\"dest_warp\":%d,\"dest_map\":%d}", w.y, w.x, w.dest_warp, w.dest_map))
  end
  a("],")

  -- Signs
  local signs = read_signs()
  a("\"signs\":[")
  for i, s in ipairs(signs) do
    if i > 1 then a(",") end
    a(string.format("{\"y\":%d,\"x\":%d,\"text_id\":%d}", s.y, s.x, s.text_id))
  end
  a("],")

  a(string.format("\"last_action\":\"%s\"", last_action_str or ""))
  a("}")

  local f = io.open(STATE_PATH, "w")
  if f then f:write(table.concat(p, "\n")) f:close()
  else log_event("ERROR: failed to open state.json for write") end
end

-- ===== Action scheduler =====
local DEFAULT_TAP_FRAMES  = 2
local DEFAULT_MOVE_FRAMES = 16
local MAX_HOLD_FRAMES     = 600
local held_key = nil
local held_remaining = 0
local cooldown_remaining = 0
local COOLDOWN_FRAMES = 1
local queued_line = nil

local function clear_action_file()
  local fw = io.open(ACTION_PATH, "w")
  if fw then fw:write("") fw:close() end
end

local function read_action()
  local f = io.open(ACTION_PATH, "r")
  if not f then return nil end
  local line = f:read("*l")
  f:close()
  if not line or line == "" then return nil end
  clear_action_file()
  return line
end

local function key_from_cmd(cmd)
  cmd = cmd:upper()
  if cmd == "UP" then return C.GB_KEY.UP end
  if cmd == "DOWN" then return C.GB_KEY.DOWN end
  if cmd == "LEFT" then return C.GB_KEY.LEFT end
  if cmd == "RIGHT" then return C.GB_KEY.RIGHT end
  if cmd == "A" then return C.GB_KEY.A end
  if cmd == "B" then return C.GB_KEY.B end
  if cmd == "START" then return C.GB_KEY.START end
  if cmd == "SELECT" then return C.GB_KEY.SELECT end
  return nil
end

local function stop_hold(reason)
  if held_key then
    emu:clearKey(held_key)
    log_event(string.format("RELEASE key=%d reason=%s", held_key, reason or "done"))
  end
  held_key = nil
  held_remaining = 0
  cooldown_remaining = COOLDOWN_FRAMES
end

local function start_hold(key, frames, src)
  if held_key then emu:clearKey(held_key) end
  held_key = key
  held_remaining = frames
  cooldown_remaining = 0
  emu:addKey(held_key)
  log_event(string.format("HOLD_START key=%d frames=%d src=%s", held_key, frames, src or "action"))
end

local function tick_hold()
  if cooldown_remaining > 0 then cooldown_remaining = cooldown_remaining - 1 end
  if not held_key then return end
  held_remaining = held_remaining - 1
  if held_remaining <= 0 then stop_hold("timer") end
end

local function clamp_frames(fr)
  if fr < 1 then fr = 1 end
  if fr > MAX_HOLD_FRAMES then fr = MAX_HOLD_FRAMES end
  return fr
end

local function parse_action(line)
  if not line then return nil end
  line = line:match("^%s*(.-)%s*$")
  if line == "" then return nil end
  local upper = line:upper()
  if upper == "STOP" then return "STOP" end

  local tap_cmd, tap_n = upper:match("^TAP%s+(%S+)%s*(%d*)$")
  if tap_cmd then
    local key = key_from_cmd(tap_cmd)
    if not key then return nil end
    local frames = tonumber(tap_n)
    if not frames then
      if tap_cmd == "UP" or tap_cmd == "DOWN" or tap_cmd == "LEFT" or tap_cmd == "RIGHT" then
        frames = DEFAULT_MOVE_FRAMES
      else frames = DEFAULT_TAP_FRAMES end
    end
    return { key = key, frames = clamp_frames(frames) }
  end

  local cmd, n = upper:match("^(%S+)%s*(%d*)$")
  if not cmd then return nil end
  local key = key_from_cmd(cmd)
  if not key then return nil end
  local frames = tonumber(n)
  if not frames then
    if cmd == "UP" or cmd == "DOWN" or cmd == "LEFT" or cmd == "RIGHT" then
      frames = DEFAULT_MOVE_FRAMES
    else frames = DEFAULT_TAP_FRAMES end
  end
  return { key = key, frames = clamp_frames(frames) }
end

local function consume_action_line(line, src)
  log_event("ACTION_READ " .. line)
  last_action_str = line
  local parsed = parse_action(line)
  if not parsed then log_event("ACTION_IGNORED parse_failed") return end
  if parsed == "STOP" then stop_hold("STOP_cmd") queued_line = nil return end
  if held_key ~= nil or cooldown_remaining > 0 then
    if queued_line == nil then
      queued_line = line
      log_event("ACTION_QUEUED " .. line)
    else
      log_event("ACTION_DROPPED queue_full " .. line)
    end
    return
  end
  start_hold(parsed.key, parsed.frames, src or "action")
end

local function pump_actions(frame)
  tick_hold()
  if held_key == nil and cooldown_remaining == 0 and queued_line ~= nil then
    local line = queued_line
    queued_line = nil
    consume_action_line(line, "queue")
    return
  end
  if (frame % ACTION_POLL_FRAMES) == 0 then
    local line = read_action()
    if line then consume_action_line(line, "file") end
  end
end

-- ===== Frame callback =====
callbacks:add("frame", function()
  local f = emu:currentFrame()
  pump_actions(f)
  if (f % UPDATE_EVERY_FRAMES) == 0 then write_state() end
end)

log_event("state_agent v4 started: fixed playtime, textbox detection, warps, menu, screen text")
console:log("state_agent v4 started (fixed playtime, textbox, warps, menu, screen text)")
