/**
 * 世界窗 WorldCam · MCP HTTP 服务
 * 零依赖：手搓 JSON-RPC 2.0 over HTTP，不引 SDK。
 *
 * 安全要点：
 *   - 绝不把用户输入拼进 shell 字符串。execFileSync 传参数数组，
 *     没有 shell 就不会二次解析，`;` `$(...)` 只是普通字符。
 *   - 不使用 sudo。
 *   - place 长度与 radius 范围都先钳制。
 */
const http = require("http");
const { execFileSync } = require("child_process");
const path = require("path");

const PORT = process.env.WORLDCAM_PORT || 4750;
const PYTHON = process.env.WORLDCAM_PYTHON || "python3";
const WORLDCAM = process.env.WORLDCAM_SCRIPT
  || path.join(__dirname, "..", "worldcam.py");
const TIMEOUT_MS = 90000;
const MAX_PLACE_LEN = 100;
const MAX_RADIUS_KM = 500;

const tools = [
  {
    name: "worldcam_look",
    description:
      "看看一个地方现在什么样：拉取公开摄像头最新帧 + 当前天气 + " +
      "附近野生动物 + 当地时区。输入地名如 'Tokyo'、'San Francisco'、" +
      "'Iceland'，中文也支持（如 '东京'、'旧金山'）。",
    inputSchema: {
      type: "object",
      properties: {
        place: { type: "string", description: "地名（中英文均可）" },
        radius_km: {
          type: "number",
          description: `搜索半径 km，默认 50，最大 ${MAX_RADIUS_KM}`,
        },
      },
      required: ["place"],
    },
  },
  {
    name: "worldcam_list",
    description: "列出某地附近所有可用公开摄像头（名称、浏览量、在线状态）。",
    inputSchema: {
      type: "object",
      properties: {
        place: { type: "string", description: "地名（中英文均可）" },
        radius_km: {
          type: "number",
          description: `搜索半径 km，默认 50，最大 ${MAX_RADIUS_KM}`,
        },
      },
      required: ["place"],
    },
  },
];

function sanitize(placeArg, args) {
  if (typeof placeArg !== "string" || !placeArg.trim()) {
    throw new Error("place is required");
  }
  const place = placeArg.trim().slice(0, MAX_PLACE_LEN);
  let radius = 50;
  if (args.radius_km !== undefined && args.radius_km !== null) {
    const n = Number(args.radius_km);
    if (!Number.isFinite(n)) throw new Error("radius_km must be a number");
    radius = Math.max(1, Math.min(Math.round(n), MAX_RADIUS_KM));
  }
  return { place, radius };
}

function run(place, radius, extraArgs) {
  const argv = [WORLDCAM, place, String(radius), ...extraArgs];
  return execFileSync(PYTHON, argv, {
    timeout: TIMEOUT_MS,
    encoding: "utf-8",
    maxBuffer: 8 * 1024 * 1024,
    shell: false,
  }).trim();
}

function callTool(name, args) {
  try {
    const { place, radius } = sanitize(args.place, args || {});
    if (name === "worldcam_look") {
      return { content: [{ type: "text", text: run(place, radius, []) }] };
    }
    if (name === "worldcam_list") {
      return { content: [{ type: "text", text: run(place, radius, ["--list"]) }] };
    }
    return {
      content: [{ type: "text", text: `Unknown tool: ${name}` }],
      isError: true,
    };
  } catch (e) {
    const msg = e.stderr && String(e.stderr).trim();
    return {
      content: [{ type: "text", text: msg || `Error: ${e.message}` }],
      isError: true,
    };
  }
}

function handleRpc(msg) {
  const { id, method, params } = msg;
  switch (method) {
    case "initialize":
      return {
        jsonrpc: "2.0",
        id,
        result: {
          protocolVersion: "2025-03-26",
          capabilities: { tools: {} },
          serverInfo: { name: "worldcam", version: "1.1.0" },
        },
      };
    case "notifications/initialized":
      return null;
    case "tools/list":
      return { jsonrpc: "2.0", id, result: { tools } };
    case "tools/call":
      // callTool 是同步的（内部用 execFileSync），不要在这里 .then()
      return {
        jsonrpc: "2.0",
        id,
        result: callTool(params.name, params.arguments || {}),
      };
    default:
      return {
        jsonrpc: "2.0",
        id,
        error: { code: -32601, message: `Method not found: ${method}` },
      };
  }
}

const server = http.createServer((req, res) => {
  if (req.method !== "POST") {
    res.writeHead(405).end();
    return;
  }
  let body = "";
  req.on("data", (c) => {
    body += c;
    if (body.length > 1e6) req.destroy();
  });
  req.on("end", () => {
    let msg;
    try {
      msg = JSON.parse(body);
    } catch {
      res.writeHead(400).end();
      return;
    }
    const out = handleRpc(msg);
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    if (out === null) {
      res.writeHead(202).end();
      return;
    }
    res.end(JSON.stringify(out));
  });
});

server.listen(PORT, () => console.log(`WorldCam MCP on :${PORT}`));
