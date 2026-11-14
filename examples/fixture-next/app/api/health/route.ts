export async function GET() {
  return Response.json({ status: "ok", region: process.env.FIXTURE_REGION });
}
