/**
 * docs-search extension for pi — 把本地文档检索接入 pi 会话
 *
 * 原理: 以子进程启动 docs-search 的 MCP stdio server(工具逻辑与 Cursor/ZCode/Qoder/DSH 完全同源),
 *       按行转发 JSON-RPC 2.0。协议实现见 src/docs_search/mcp.py。
 *
 * 前置: pip install docs-search(提供 docs-search-mcp 命令)
 *       或设 DOCS_SEARCH_MCP_CMD / DOCS_SEARCH_MCP_ARGS 指定启动命令与参数。
 * 目录: DOCS_SEARCH_DIR 环境变量 > ./docs(pi 启动目录)。
 * 工作空间: DOCS_SEARCH_WORKSPACE(可选)按命名空间分割索引库,不填=默认库。
 * 远程模式: 设 DOCS_SEARCH_URL(如 http://192.168.1.10:8765)时改连已运行的 docs-search
 *       服务(--url 启动 MCP server,不读本地目录),适合服务已启动/异机共享文档库的场景。
 *       远端启用认证时,凭据用 DOCS_SEARCH_TOKEN(Bearer)或 DOCS_SEARCH_USER+DOCS_SEARCH_PASSWORD(Basic)。
 * 安装: 复制到 ~/.pi/agent/extensions/docs-search.ts(全局)或 .pi/extensions/docs-search.ts(项目),
 *       /reload 热加载。详见 docs/MCP.md。
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { spawn, type ChildProcess } from "node:child_process";
import { Type } from "typebox";

const PROTOCOL_VERSION = "2024-11-05";

const SearchParams = Type.Object({
	query: Type.String({ description: "Keywords, space-separated (AND semantics; CJK supported)" }),
	limit: Type.Optional(Type.Number({ description: "Max results (default 8, cap 20)" })),
	cat: Type.Optional(Type.String({ description: "Category filter (top-level folder name)" })),
});

const ReadParams = Type.Object({
	path: Type.String({ description: "Document relative path, e.g. infra/mcp.md or uploads/notes.md" }),
});

const WriteParams = Type.Object({
	filename: Type.String({ description: "Target file name, must end with .md (basename only)" }),
	content: Type.String({ description: "Markdown text (UTF-8); indexed immediately" }),
});

const DeleteParams = Type.Object({
	path: Type.String({ description: "Document relative path (must be under uploads/)" }),
});

const InfoParams = Type.Object({
	mode: Type.Optional(Type.Union([Type.Literal("list"), Type.Literal("stats")], { description: "list (default) or stats" })),
	cat: Type.Optional(Type.String({ description: "Category filter (list mode only)" })),
});

interface Pending {
	resolve: (text: string) => void;
}

export default function docsSearchExtension(pi: ExtensionAPI) {
	let proc: ChildProcess | null = null;
	let spawnError: string | null = null;
	let nextId = 1;
	const pending = new Map<number, Pending>();

	const docsDir = process.env.DOCS_SEARCH_DIR || "docs";
	const serviceUrl = process.env.DOCS_SEARCH_URL || "";
	const workspace = process.env.DOCS_SEARCH_WORKSPACE || "";
	const target = serviceUrl ? `url ${serviceUrl}` : `dir ${docsDir}${workspace ? ` ws:${workspace}` : ""}`;
	// 远程认证凭据(远端服务启用了认证时透传给 --token/--user/--password)
	const credArgs: string[] = [];
	if (process.env.DOCS_SEARCH_TOKEN) credArgs.push("--token", process.env.DOCS_SEARCH_TOKEN);
	if (process.env.DOCS_SEARCH_USER && process.env.DOCS_SEARCH_PASSWORD) {
		credArgs.push("--user", process.env.DOCS_SEARCH_USER, "--password", process.env.DOCS_SEARCH_PASSWORD);
	}

	function ensureServer(): ChildProcess {
		if (proc && proc.exitCode === null) {
			return proc;
		}
		spawnError = null;
		const cmd = process.env.DOCS_SEARCH_MCP_CMD || "docs-search-mcp";
		const extraArgs = (process.env.DOCS_SEARCH_MCP_ARGS || "").split(" ").filter(Boolean);
		const args = serviceUrl
			? [...extraArgs, ...credArgs, "--url", serviceUrl]
			: [...extraArgs, ...(workspace ? ["--workspace", workspace] : []), "--dir", docsDir];
		const child = spawn(cmd, args, {
			stdio: ["pipe", "pipe", "pipe"],
		});

		let buf = "";
		child.stdout?.on("data", (chunk: Buffer) => {
			buf += chunk.toString("utf-8");
			let idx: number;
			while ((idx = buf.indexOf("\n")) >= 0) {
				const line = buf.slice(0, idx).trim();
				buf = buf.slice(idx + 1);
				if (!line) {
					continue;
				}
				try {
					const msg = JSON.parse(line) as { id?: number; result?: unknown; error?: { message?: string } };
					if (typeof msg.id === "number" && pending.has(msg.id)) {
						const p = pending.get(msg.id)!;
						pending.delete(msg.id);
						const result = msg.result as { content?: Array<{ text?: string }> } | undefined;
						p.resolve(result?.content?.[0]?.text ?? msg.error?.message ?? "(empty MCP response)");
					}
				} catch {
					// 忽略非 JSON 行(保持与 server 侧同样宽容)
				}
			}
		});
		child.stderr?.on("data", () => {}); // server 日志走 stderr,静默
		child.on("error", (err: Error) => {
			spawnError = `无法启动 ${cmd}: ${err.message}(pip install docs-search 或设置 DOCS_SEARCH_MCP_CMD)`;
		});
		child.on("close", () => {
			for (const [, p] of pending) {
				p.resolve("docs-search MCP server 已退出,下次调用将自动重启。");
			}
			pending.clear();
		});

		proc = child;
		const id = nextId++;
		pending.set(id, { resolve: () => {} }); // initialize 响应丢弃
		child.stdin?.write(
			`${JSON.stringify({ jsonrpc: "2.0", id, method: "initialize", params: { protocolVersion: PROTOCOL_VERSION } })}\n`,
		);
		child.stdin?.write(`${JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" })}\n`);
		return child;
	}

	function call(tool: string, args: Record<string, unknown>): Promise<string> {
		const child = ensureServer();
		if (spawnError) {
			return Promise.resolve(spawnError);
		}
		const id = nextId++;
		return new Promise((resolve) => {
			pending.set(id, { resolve });
			child.stdin?.write(
				`${JSON.stringify({ jsonrpc: "2.0", id, method: "tools/call", params: { name: tool, arguments: args } })}\n`,
			);
		});
	}

	const wrap = (tool: string) => async (_toolCallId: string, params: Record<string, unknown>) => ({
		content: [{ type: "text" as const, text: await call(tool, params) }],
		details: { server: "docs-search", target },
	});

	pi.registerTool({
		name: "docs_search",
		label: "Docs Search",
		description:
			"Search the local docs-search library (agent memory/knowledge base). Multi-keyword AND substring match, CJK included. Returns paths + snippets; use docs_read for full text. Index auto-refreshes.",
		promptSnippet: `Search local markdown library at ${docsDir}`,
		parameters: SearchParams,
		execute: wrap("docs_search"),
	});

	pi.registerTool({
		name: "docs_read",
		label: "Docs Read",
		description: "Read full text of a document from the docs-search library by relative path.",
		parameters: ReadParams,
		execute: wrap("docs_read"),
	});

	pi.registerTool({
		name: "docs_write",
		label: "Docs Write",
		description:
			"Write markdown into the docs-search library (uploads/, sanitized, indexed immediately). Persist session conclusions for future retrieval. Do NOT write secrets — library is readable by local processes.",
		parameters: WriteParams,
		execute: wrap("docs_write"),
	});

	pi.registerTool({
		name: "docs_delete",
		label: "Docs Delete",
		description: "Delete a docs-search library document (uploads/ only; library files are rejected by design).",
		parameters: DeleteParams,
		execute: wrap("docs_delete"),
	});

	pi.registerTool({
		name: "docs_info",
		label: "Docs Info",
		description: "Enumerate the docs-search library: mode=list (all docs) or mode=stats (count/categories).",
		parameters: InfoParams,
		execute: wrap("docs_info"),
	});
}
