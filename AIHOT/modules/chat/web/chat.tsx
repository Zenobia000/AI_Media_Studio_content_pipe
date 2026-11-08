import { useEffect, useRef, useState } from "react";
import { PhoneBar } from "../../../apps/web/app/components/shell/PhoneBar";
import type { Screen } from "../../../apps/web/app/components/shell/screens";
import { pageMeta } from "../../../apps/web/app/lib/seo";

export const handle: Screen = { tab: "me", name: "AI 問答" };

export function meta() {
  return pageMeta({ title: "AI 問答", description: "只根據本站文章回答問題，並列出出處。", path: "/chat" });
}

interface Source { n: number; title: string; url: string; score: number }
interface Message { role: "user" | "assistant"; text: string; sources?: Source[]; error?: boolean }

const SAMPLES = ["幫我推薦幾篇文章", "NVIDIA 最近發布了什麼模型？", "GitHub Copilot 怎麼提高開發效率？"];

/** One turn: the earlier messages go along so a follow-up is understood; the answer arrives as JSON lines (sources first, then pieces of text). */
async function ask(history: Message[], question: string, onSources: (s: Source[]) => void, onText: (piece: string) => void): Promise<"ok" | "unavailable" | "failed"> {
  const messages = [...history.filter((m) => !m.error).map((m) => ({ role: m.role, content: m.text })), { role: "user", content: question }];
  try {
    const res = await fetch("/api/chat", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ messages }) });
    if (!res.ok || !res.body) return "failed";
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let rest = "";
    for (;;) {
      const { done, value } = await reader.read();
      rest += decoder.decode(value, { stream: !done });
      const lines = rest.split("\n");
      rest = done ? "" : lines.pop() ?? "";
      for (const line of lines) {
        if (!line.trim()) continue;
        const event = JSON.parse(line) as { type: string; text?: string; sources?: Source[]; code?: string };
        if (event.type === "sources") onSources(event.sources ?? []);
        else if (event.type === "delta") onText(event.text ?? "");
        else if (event.type === "error") return event.code === "model_unavailable" ? "unavailable" : "failed";
      }
      if (done) return "ok";
    }
  } catch {
    return "failed";
  }
}

/** **bold** in a reply; the capture group makes split keep the bold parts, and with no g flag, test has no state to carry over. */
const BOLD = /(\*\*[^*\n]+\*\*)/;

/** The model's text with **bold** shown as bold; built from elements, never from HTML, since the text is not ours. */
function Reply({ text }: { text: string }) {
  return (
    <p className="whitespace-pre-wrap">
      {text.split(BOLD).map((part, i) => (BOLD.test(part) ? <strong key={i}>{part.slice(2, -2)}</strong> : part))}
    </p>
  );
}

export default function Chat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [waiting, setWaiting] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  // The conversation lives only in this page: a refresh or a new visit starts an empty chat (there are no accounts to keep one for).
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [messages, waiting]);

  async function send(question: string) {
    const q = question.trim();
    if (!q || waiting) return;
    setText("");
    const history = messages;
    // The answer's message is added at once and grows as pieces arrive.
    setMessages([...history, { role: "user", text: q }, { role: "assistant", text: "" }]);
    setWaiting(true);
    const patch = (change: (m: Message) => Message) => setMessages((all) => all.map((m, i) => (i === all.length - 1 ? change(m) : m)));
    const result = await ask(history, q, (sources) => patch((m) => ({ ...m, sources })), (piece) => patch((m) => ({ ...m, text: m.text + piece })));
    if (result === "unavailable") patch(() => ({ role: "assistant", text: "回答用的模型現在沒有開，請稍後再試。", error: true }));
    else if (result === "failed") patch((m) => ({ role: "assistant", text: m.text || "出了點問題，請稍後再試。", error: true }));
    setWaiting(false);
  }

  return (
    <>
      <PhoneBar back={{ to: "/more", label: "我的" }} title="AI 問答" />
      <div className="mx-auto flex min-h-[calc(100dvh-6rem)] max-w-[760px] flex-col pb-4 lg:pt-6">
        <header className="mb-4 flex items-end justify-between gap-3">
          <div>
            <h1 data-page-title="" className="text-[24px] font-semibold leading-[1.3] text-ink">AI 問答</h1>
            <p className="mt-1 text-[13px] text-ink-3">可以聊天、請它推薦文章、問新聞內容；新聞與資訊只來自本站的文章。</p>
          </div>
          {messages.length > 0 && (
            <button type="button" onClick={() => setMessages([])} className="shrink-0 text-[12.5px] text-ink-3 hover:text-ink">清除對話</button>
          )}
        </header>

        <div className="flex-1 space-y-4" aria-live="polite">
          {messages.length === 0 && (
            <div className="rounded-control border border-line bg-surface p-4">
              <p className="text-[14px] text-ink-2">可以直接聊，或試試：</p>
              <div className="mt-3 flex flex-wrap gap-2">
                {SAMPLES.map((s) => (
                  <button key={s} type="button" onClick={() => send(s)} className="rounded-full border border-line-strong px-3 py-1.5 text-[13px] text-ink-2 hover:border-ink-4 hover:text-ink">{s}</button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m, i) => m.role === "assistant" && m.text === "" ? null : (
            <div key={i} className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
              <div className={`max-w-[88%] rounded-2xl px-4 py-2.5 text-[15px] leading-[1.7] ${
                m.role === "user" ? "bg-accent text-white" : `border border-line bg-surface ${m.error ? "text-ink-3" : "text-ink"}`
              }`}>
                {m.role === "assistant" ? <Reply text={m.text} /> : <p className="whitespace-pre-wrap">{m.text}</p>}
                {m.sources && m.sources.length > 0 && (
                  <ol className="mt-3 space-y-1 border-t border-line pt-2 text-[12.5px] text-ink-3">
                    {m.sources.map((s) => (
                      <li key={s.n}>
                        [{s.n}] <a href={s.url} target="_blank" rel="noopener noreferrer" className="underline decoration-line-strong underline-offset-2 hover:text-ink">{s.title}</a>
                      </li>
                    ))}
                  </ol>
                )}
              </div>
            </div>
          ))}
          {waiting && messages.at(-1)?.text === "" && <div className="text-[13px] text-ink-3">思考中…</div>}
          <div ref={bottom} />
        </div>

        <form onSubmit={(e) => { e.preventDefault(); send(text); }} className="sticky bottom-0 mt-4 flex gap-2 bg-bg pb-[max(8px,env(safe-area-inset-bottom))] pt-2">
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            maxLength={500}
            placeholder="輸入問題"
            aria-label="輸入問題"
            className="h-11 min-w-0 flex-1 rounded-full border border-line-strong bg-surface px-4 text-[16px] text-ink outline-none focus:border-accent"
          />
          <button type="submit" disabled={waiting || !text.trim()} className="h-11 shrink-0 rounded-full bg-accent px-5 text-[14px] font-medium text-white disabled:opacity-40">送出</button>
        </form>
      </div>
    </>
  );
}
