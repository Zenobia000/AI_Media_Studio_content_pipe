// The 素材區: one page, /materials. Its data comes from the api's /api/materials, which the web server already passes on (every /api/ address).
import { defineModule } from "@aihot/contracts/modules";

export default defineModule({
  name: "materials",
  pages: [{ path: "materials", file: "web/materials.tsx" }],
});
