// The chat window's entries in the navigation: the desktop sidebar's 內容 section and the 我的 page's tools.
import { defineWebModule } from "@aihot/web/modules";
import { IconSparkles } from "../../apps/web/app/components/icons";

export default defineWebModule({
  name: "chat",
  sidebar: { section: "內容", items: [{ to: "/chat", label: "AI 問答", icon: IconSparkles }] },
  tools: [{ to: "/chat", label: "AI 問答", icon: <IconSparkles size={18} /> }],
});
