import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { IconDownload } from "../../../apps/web/app/components/icons";
import { PhoneBar } from "../../../apps/web/app/components/shell/PhoneBar";
import type { Screen } from "../../../apps/web/app/components/shell/screens";
import { removeStar, useStarred } from "../../../apps/web/app/lib/local-state";
import { pageMeta } from "../../../apps/web/app/lib/seo";

export const handle: Screen = { tab: "me", name: "素材" };

export function meta() {
  return pageMeta({ title: "素材", description: "收藏的文章，和它們的原文、照片、影片下載。", path: "/materials", noindex: true });
}

type Kind = "article" | "photo" | "video";
type State = "idle" | "queued" | "running" | "done" | "failed" | "interrupted";

interface Pick {
  id: string;
  title: string;
  summary: string | null;
  url: string;
  source: string;
  folder: string | null;
  state: State;
  wanted: Kind[];
  result: { errors?: string[]; notes?: string[] };
  files: Array<{ kind: Kind; path: string; name: string; size: number }>;
  counts: Record<Kind, number>;
  gaps: Array<{ need: string; reason: string; suggestion: string }>;
}
interface Listing { picks: Pick[]; tools: { ytdlp: boolean; galleryDl: boolean } }

const KINDS: Array<{ kind: Kind; label: string; hint: string }> = [
  { kind: "article", label: "文章", hint: "網頁的文字，存成 Markdown" },
  { kind: "photo", label: "照片", hint: "網頁上的主要圖片" },
  { kind: "video", label: "影片", hint: "影片和 GIF 動圖" },
];
const BUSY: State[] = ["queued", "running"];

function size(bytes: number): string {
  return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function statusLine(p: Pick): string {
  if (p.state === "queued") return "排隊中…";
  if (p.state === "running") return "下載中…";
  if (p.state === "interrupted") return "中斷了（服務重新啟動過），可以再下載一次";
  if (p.state === "idle") return "還沒下載";
  const got = [p.counts.article ? "文章 ✓" : null, p.counts.photo ? `照片 ${p.counts.photo}` : null, p.counts.video ? `影片 ${p.counts.video}` : null].filter(Boolean);
  const head = p.state === "failed" ? "失敗" : "完成";
  return got.length ? `${head} · ${got.join(" · ")}` : `${head} · 沒有抓到檔案`;
}

export default function Materials() {
  const starred = useStarred();
  const [listing, setListing] = useState<Listing | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [choice, setChoice] = useState<Record<string, Kind[]>>({});
  const synced = useRef(false);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/materials");
      if (res.ok) setListing((await res.json()) as Listing);
      else setNotice("讀不到素材清單，請稍後再試。");
    } catch {
      setNotice("連不上伺服器。");
    }
  }, []);

  // The bookmarks of this browser go to the list once when the page opens, so articles starred before the area existed are in it too.
  useEffect(() => {
    if (synced.current) return;
    synced.current = true;
    const ids = starred.map((s) => s.id);
    (ids.length ? fetch("/api/materials/sync", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ ids }) }).catch(() => {}) : Promise.resolve()).then(load);
  }, [starred, load]);

  // While anything is being downloaded the list is read again every few seconds.
  const busy = listing?.picks.some((p) => BUSY.includes(p.state)) ?? false;
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [busy, load]);

  const kindsOf = (p: Pick): Kind[] => choice[p.id] ?? (["article", "photo", "video"] as Kind[]);
  const toggle = (p: Pick, kind: Kind) => {
    const now = kindsOf(p);
    setChoice({ ...choice, [p.id]: now.includes(kind) ? now.filter((k) => k !== kind) : [...now, kind] });
  };

  async function download(p: Pick) {
    setNotice(null);
    const res = await fetch(`/api/materials/${encodeURIComponent(p.id)}/fetch`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-materials-request": "1" },
      body: JSON.stringify({ kinds: kindsOf(p) }),
    }).catch(() => null);
    if (!res) setNotice("連不上伺服器。");
    else if (res.status === 409) setNotice("這篇已經在排隊或下載了。");
    else if (!res.ok) setNotice("沒能開始下載。");
    await load();
  }

  async function remove(p: Pick) {
    const res = await fetch(`/api/materials/${encodeURIComponent(p.id)}`, { method: "DELETE" }).catch(() => null);
    const done = res?.ok ? ((await res.json()) as { removed: boolean }).removed : false;
    if (!done) setNotice("下載中的文章不能移除，等它完成。");
    else removeStar(p.id);
    await load();
  }

  async function downloadAll() {
    for (const p of listing?.picks ?? []) if (!BUSY.includes(p.state) && kindsOf(p).length) await download(p);
  }

  const picks = listing?.picks ?? [];
  return (
    <>
      <PhoneBar back={{ to: "/more", label: "我的" }} title="素材" />
      <div className="mx-auto max-w-[820px] pb-14 lg:pt-6">
        <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 data-page-title="" className="text-[22px] font-semibold leading-[1.3] text-ink">素材</h1>
            <p className="mt-1 text-[13.5px] text-ink-3">在文章旁邊按書籤（收藏），它就會出現在這裡。勾選要下載的類型，再按下載。</p>
          </div>
          {picks.length > 1 && (
            <button type="button" onClick={downloadAll} className="rounded-control border border-line-strong px-3.5 py-2 text-[13px] text-ink-2 hover:border-ink-4 hover:text-ink">
              全部下載
            </button>
          )}
        </header>

        {listing && (!listing.tools.ytdlp || !listing.tools.galleryDl) && (
          <p className="mb-4 text-[12.5px] text-ink-3">
            {[!listing.tools.ytdlp && "還沒裝 yt-dlp（YouTube 等站的影片抓不到）", !listing.tools.galleryDl && "還沒裝 gallery-dl（部分站的照片抓不到）"].filter(Boolean).join("；")}。
            網頁自己提供的圖片和影片仍然會抓。安裝：python -m pip install --user yt-dlp gallery-dl
          </p>
        )}
        {notice && <p role="status" className="mb-4 text-[13.5px] text-accent">{notice}</p>}

        {listing && picks.length === 0 && (
          <div className="flex min-h-[40vh] flex-col items-center justify-center text-center">
            <span className="mb-4 flex h-10 w-10 items-center justify-center rounded-full bg-accent/10 text-accent"><IconDownload size={20} /></span>
            <p className="max-w-[360px] text-[14px] text-ink-3">還沒有素材。看到要用的文章，按它旁邊的書籤，就會放進這裡。</p>
          </div>
        )}

        <ul className="space-y-3">
          {picks.map((p) => {
            const working = BUSY.includes(p.state);
            const kinds = kindsOf(p);
            const notes = [...(p.result.errors ?? []), ...(p.result.notes ?? [])];
            return (
              <li key={p.id} className="rounded-card border border-line bg-surface p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <Link to={`/items/${p.id}`} className="text-[15px] font-medium leading-[1.5] text-ink hover:underline">{p.title}</Link>
                    <p className="mt-0.5 text-[12.5px] text-ink-3">{p.source}　<a href={p.url} target="_blank" rel="noopener noreferrer" className="underline decoration-line-strong underline-offset-2 hover:text-ink">原文</a></p>
                  </div>
                  <button type="button" onClick={() => remove(p)} disabled={working} className="shrink-0 text-[12.5px] text-ink-3 hover:text-ink disabled:opacity-40">移除</button>
                </div>

                <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
                  {KINDS.map((k) => (
                    <label key={k.kind} title={k.hint} className="flex items-center gap-1.5 text-[13.5px] text-ink-2">
                      <input type="checkbox" checked={kinds.includes(k.kind)} disabled={working} onChange={() => toggle(p, k.kind)} className="size-4 accent-[var(--accent)]" />
                      {k.label}
                    </label>
                  ))}
                  <button
                    type="button"
                    onClick={() => download(p)}
                    disabled={working || kinds.length === 0}
                    className="ml-auto inline-flex items-center gap-1.5 rounded-control bg-accent px-3.5 py-1.5 text-[13px] text-white disabled:opacity-40"
                  >
                    <IconDownload size={14} />
                    {p.state === "done" || p.state === "failed" ? "再下載" : "下載"}
                  </button>
                </div>

                <p className={`mt-3 text-[13px] ${p.state === "failed" || p.state === "interrupted" ? "text-accent" : "text-ink-3"}`} aria-live="polite">
                  {working && <span className="mr-1.5 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-accent align-middle" aria-hidden="true" />}
                  {statusLine(p)}
                </p>

                {p.gaps.length > 0 && (
                  <ul className="mt-2 space-y-1 text-[12.5px] text-ink-3">
                    {p.gaps.map((g, i) => <li key={i}>缺 {g.need}：{g.reason}。{g.suggestion}</li>)}
                  </ul>
                )}
                {(p.files.length > 0 || notes.length > 0) && (
                  <details className="mt-2 text-[12.5px] text-ink-3">
                    <summary className="cursor-pointer select-none hover:text-ink">檔案與紀錄{p.folder ? `（工作區 ${p.folder}）` : ""}</summary>
                    {p.files.length > 0 && (
                      <ul className="mt-1.5 space-y-0.5">
                        {p.files.map((f) => <li key={f.path} className="break-all">{KINDS.find((k) => k.kind === f.kind)?.label}　{f.name}　{size(f.size)}</li>)}
                      </ul>
                    )}
                    {notes.length > 0 && <ul className="mt-1.5 space-y-0.5">{notes.map((n, i) => <li key={i} className="break-all">{n}</li>)}</ul>}
                  </details>
                )}
              </li>
            );
          })}
        </ul>
      </div>
    </>
  );
}
