import { useEffect, useRef, useState } from "react";
import { IconSparkles } from "../../../apps/web/app/components/icons";
import { PhoneBar } from "../../../apps/web/app/components/shell/PhoneBar";
import type { Screen } from "../../../apps/web/app/components/shell/screens";
import { pageMeta } from "../../../apps/web/app/lib/seo";

export const handle: Screen = { tab: "me", name: "AI 問答" };

export function meta() {
  return pageMeta({ title: "AI 問答", description: "只根據本站文章回答問題，並列出出處。", path: "/chat" });
}

interface Source { n: number; title: string; url: string; score: number }
interface Message {
  role: "user" | "assistant";
  text: string;
  sources?: Source[];
  /** Numbers of the sources the answer points to, once it is complete. */
  cited?: number[];
  error?: boolean;
  /** What the server said it was doing, in order ("searching the articles", "found 5"); the last one is the one in progress while `streaming`. */
  steps?: string[];
  streaming?: boolean;
}

/** The sources to list under an answer: those it points to as [n], or all of them when it points to none; nothing while it is still being written. */
function listed(m: Message): Source[] {
  if (!m.sources || m.cited === undefined) return [];
  const used = m.sources.filter((s) => m.cited!.includes(s.n));
  return used.length > 0 ? used : m.sources;
}

/** How many earlier messages go with a question: the api only reads the last few, and a long chat stays within its limit. */
const HISTORY = 12;

const SAMPLES = ["幫我推薦幾篇文章", "NVIDIA 最近發布了什麼模型？", "GitHub Copilot 怎麼提高開發效率？"];

interface Handlers { status: (text: string) => void; sources: (s: Source[]) => void; text: (piece: string) => void; cited: (numbers: number[]) => void }

/** One turn: the earlier messages go along so a follow-up is understood; the answer arrives as JSON lines (what is being done, sources, then pieces of text). */
async function ask(history: Message[], question: string, signal: AbortSignal, on: Handlers): Promise<"ok" | "unavailable" | "failed" | "stopped"> {
  const messages = [...history.filter((m) => !m.error).slice(-HISTORY).map((m) => ({ role: m.role, content: m.text })), { role: "user", content: question }];
  try {
    const res = await fetch("/api/chat", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ messages }), signal });
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
        const event = JSON.parse(line) as { type: string; text?: string; sources?: Source[]; numbers?: number[]; code?: string };
        if (event.type === "status") on.status(event.text ?? "");
        else if (event.type === "sources") on.sources(event.sources ?? []);
        else if (event.type === "cited") on.cited(event.numbers ?? []);
        else if (event.type === "delta") on.text(event.text ?? "");
        else if (event.type === "error") return event.code === "model_unavailable" ? "unavailable" : "failed";
      }
      if (done) return "ok";
    }
  } catch (e) {
    return e instanceof DOMException && e.name === "AbortError" ? "stopped" : "failed";
  }
}

/** **bold** in a reply; the capture group makes split keep the bold parts, and with no g flag, test has no state to carry over. */
const BOLD = /(\*\*[^*\n]+\*\*)/;

/** The model's text with **bold** shown as bold; built from elements, never from HTML, since the text is not ours. */
function Reply({ text, streaming }: { text: string; streaming?: boolean }) {
  return (
    <p className="whitespace-pre-wrap">
      {text.split(BOLD).map((part, i) => (BOLD.test(part) ? <strong key={i}>{part.slice(2, -2)}</strong> : part))}
      {streaming && <span className="ml-0.5 inline-block h-[1.1em] w-[2px] translate-y-[3px] animate-pulse bg-ink-3" aria-hidden="true" />}
    </p>
  );
}

/** What is being done, as the server says it: finished steps in grey, the one in progress pulsing; once the answer is complete the steps fold into one line. */
function Steps({ steps, streaming }: { steps: string[]; streaming: boolean }) {
  if (steps.length === 0) return null;
  if (!streaming) {
    return (
      <details className="mb-2 text-[13px] text-ink-3">
        <summary className="cursor-pointer select-none hover:text-ink-2">已完成 {steps.length} 個步驟</summary>
        <ul className="mt-1.5 space-y-1 border-l border-line pl-3">
          {steps.map((t, i) => <li key={i}>{t}</li>)}
        </ul>
      </details>
    );
  }
  return (
    <ul className="mb-2 space-y-1 text-[13px]" aria-label="進行中的步驟">
      {steps.map((t, i) => {
        const current = i === steps.length - 1;
        return (
          <li key={i} className={`flex items-center gap-2 ${current ? "text-ink-2" : "text-ink-3"}`}>
            <span className={`inline-block h-1.5 w-1.5 rounded-full ${current ? "animate-pulse bg-accent" : "bg-line-strong"}`} aria-hidden="true" />
            {t}{current && "…"}
          </li>
        );
      })}
    </ul>
  );
}

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      onClick={() => { void navigator.clipboard?.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1500); }); }}
      className="text-[12.5px] text-ink-3 hover:text-ink"
    >
      {done ? "已複製" : "複製"}
    </button>
  );
}

export default function Chat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [waiting, setWaiting] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  const abort = useRef<AbortController | null>(null);

  // The conversation lives only in this page: a refresh or a new visit starts an empty chat (there are no accounts to keep one for).
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [messages, waiting]);

  // The box grows with what is typed, up to a few lines, like the chat sites people know.
  useEffect(() => {
    const el = field.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [text]);

  async function send(question: string) {
    const q = question.trim();
    if (!q || waiting) return;
    setText("");
    const history = messages;
    // The answer's message is added at once and grows as steps and pieces arrive.
    setMessages([...history, { role: "user", text: q }, { role: "assistant", text: "", steps: [], streaming: true }]);
    setWaiting(true);
    const patch = (change: (m: Message) => Message) => setMessages((all) => all.map((m, i) => (i === all.length - 1 ? change(m) : m)));
    const controller = new AbortController();
    abort.current = controller;
    const result = await ask(history, q, controller.signal, {
      status: (t) => patch((m) => ({ ...m, steps: [...(m.steps ?? []), t] })),
      sources: (sources) => patch((m) => ({ ...m, sources })),
      text: (piece) => patch((m) => ({ ...m, text: m.text + piece })),
      cited: (cited) => patch((m) => ({ ...m, cited })),
    });
    if (result === "unavailable") patch(() => ({ role: "assistant", text: "回答用的模型現在沒有開，請稍後再試。", error: true }));
    else if (result === "failed") patch((m) => ({ ...m, text: m.text || "出了點問題，請稍後再試。", error: true, streaming: false }));
    else if (result === "stopped") patch((m) => ({ ...m, text: m.text || "（已停止）", streaming: false, cited: m.cited ?? [] }));
    else patch((m) => ({ ...m, streaming: false }));
    abort.current = null;
    setWaiting(false);
    field.current?.focus();
  }

  return (
    <>
      <PhoneBar back={{ to: "/more", label: "我的" }} title="AI 問答" />
      <div className="mx-auto flex min-h-[calc(100dvh-6rem)] max-w-[760px] flex-col pb-4 lg:pt-6">
        {messages.length > 0 && (
          <header className="mb-4 flex items-center justify-between gap-3">
            <h1 data-page-title="" className="text-[18px] font-semibold leading-[1.3] text-ink">AI 問答</h1>
            <button type="button" onClick={() => { abort.current?.abort(); setMessages([]); }} className="shrink-0 text-[12.5px] text-ink-3 hover:text-ink">新對話</button>
          </header>
        )}

        <div className="flex-1 space-y-6" aria-live="polite">
          {messages.length === 0 && (
            <div className="flex min-h-[46vh] flex-col items-center justify-center text-center">
              <span className="mb-4 flex h-10 w-10 items-center justify-center rounded-full bg-accent/10 text-accent"><IconSparkles size={20} /></span>
              <h1 data-page-title="" className="text-[24px] font-semibold leading-[1.3] text-ink">想問什麼？</h1>
              <p className="mt-2 max-w-[420px] text-[13.5px] text-ink-3">可以聊天、請我推薦文章、問新聞內容。新聞與資訊只來自本站的文章。</p>
              <div className="mt-6 flex flex-wrap justify-center gap-2">
                {SAMPLES.map((s) => (
                  <button key={s} type="button" onClick={() => send(s)} className="rounded-full border border-line-strong px-3.5 py-2 text-[13px] text-ink-2 hover:border-ink-4 hover:text-ink">{s}</button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m, i) => m.role === "user" ? (
            <div key={i} className="flex justify-end">
              <div className="max-w-[85%] rounded-card bg-surface-2 px-4 py-2.5 text-[15px] leading-[1.7] text-ink"><p className="whitespace-pre-wrap">{m.text}</p></div>
            </div>
          ) : (
            <div key={i} className="flex gap-3">
              <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-line text-accent"><IconSparkles size={14} /></span>
              <div className={`min-w-0 flex-1 text-[15px] leading-[1.75] ${m.error ? "text-ink-3" : "text-ink"}`}>
                <Steps steps={m.steps ?? []} streaming={!!m.streaming} />
                {m.text !== "" && <Reply text={m.text} streaming={m.streaming} />}
                {listed(m).length > 0 && (
                  <ol className="mt-3 space-y-1 border-t border-line pt-2 text-[12.5px] text-ink-3">
                    {listed(m).map((s) => (
                      <li key={s.n}>
                        [{s.n}] <a href={s.url} target="_blank" rel="noopener noreferrer" className="underline decoration-line-strong underline-offset-2 hover:text-ink">{s.title}</a>
                      </li>
                    ))}
                  </ol>
                )}
                {!m.streaming && m.text !== "" && !m.error && <div className="mt-2"><CopyButton text={m.text} /></div>}
              </div>
            </div>
          ))}
          <div ref={bottom} />
        </div>

        <form onSubmit={(e) => { e.preventDefault(); send(text); }} className="sticky bottom-0 mt-4 bg-bg pb-[max(8px,env(safe-area-inset-bottom))] pt-2">
          <div className="flex items-end gap-2 rounded-card border border-line-strong bg-surface py-2 pl-4 pr-2 focus-within:border-accent">
            <textarea
              ref={field}
              value={text}
              rows={1}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                // Enter sends, Shift+Enter breaks the line; the Enter that confirms a Chinese or Japanese composition must not send.
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(text); }
              }}
              maxLength={500}
              placeholder="輸入問題，Enter 送出"
              aria-label="輸入問題"
              className="max-h-40 min-w-0 flex-1 resize-none bg-transparent py-1.5 text-[16px] leading-[1.5] text-ink outline-none placeholder:text-ink-4"
            />
            {waiting ? (
              <button type="button" onClick={() => abort.current?.abort()} aria-label="停止" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-ink text-bg">
                <span className="block h-3 w-3 rounded-mark bg-current" />
              </button>
            ) : (
              <button type="submit" disabled={!text.trim()} aria-label="送出" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent text-white disabled:opacity-30">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M8 13V3M3.5 7.5 8 3l4.5 4.5" /></svg>
              </button>
            )}
          </div>
        </form>
      </div>
    </>
  );
}
