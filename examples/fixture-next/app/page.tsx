export default function Home() {
  return (
    <main>
      <h1>Fixture shop</h1>
      <img src="/hero.png" width={320} height={120} />
      <form>
        <input type="text" name="q" placeholder="Search" />
      </form>
      <a href="/products/1">First product</a>
    </main>
  );
}
