// The 素材區's entries in the navigation (the desktop sidebar's 內容 section, the 我的 page's tools) and the part that keeps its list in step with the
// bookmarks: the engine announces every star and unstar as a window event ("aihot:star", lib/local-state.ts), and from any page this sends it on.
import { useEffect } from "react";
import { defineWebModule } from "@aihot/web/modules";
import { IconDownload } from "../../apps/web/app/components/icons";

function StarSync() {
  useEffect(() => {
    const onStar = (event: Event) => {
      const detail = (event as CustomEvent<{ id?: unknown; on?: unknown }>).detail;
      if (!detail || typeof detail.id !== "string") return;
      // keepalive: the request outlives a page that is left right after the click.
      fetch(`/api/materials/${encodeURIComponent(detail.id)}`, { method: detail.on ? "PUT" : "DELETE", keepalive: true }).catch(() => {});
    };
    window.addEventListener("aihot:star", onStar);
    return () => window.removeEventListener("aihot:star", onStar);
  }, []);
  return null;
}

export default defineWebModule({
  name: "materials",
  sidebar: { section: "內容", items: [{ to: "/materials", label: "素材", icon: IconDownload }] },
  tools: [{ to: "/materials", label: "素材", icon: <IconDownload size={18} /> }],
  root: { Top: StarSync },
});
