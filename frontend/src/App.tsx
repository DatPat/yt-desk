import { Desktop } from "./Desktop";
import { Mobile } from "./Mobile";

export default function App() {
  const path = window.location.pathname;
  if (path === "/m" || path.startsWith("/m/")) {
    return <Mobile />;
  }
  return <Desktop />;
}
