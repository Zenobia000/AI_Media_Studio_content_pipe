// The chat window: one page, /chat. Its answers come from the api's POST /api/chat, which the web server already passes on (every /api/ address).
import { defineModule } from "@aihot/contracts/modules";

export default defineModule({
  name: "chat",
  pages: [{ path: "chat", file: "web/chat.tsx" }],
});
