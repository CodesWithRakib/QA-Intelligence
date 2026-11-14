import { sum } from "./math.js";

export default function App() {
  return (
    <main>
      <h1>Fixture Vite</h1>
      <p>2 + 3 = {sum(2, 3)}</p>
      <button onClick={() => alert("clicked")}>
        <svg width="16" height="16" />
      </button>
    </main>
  );
}
