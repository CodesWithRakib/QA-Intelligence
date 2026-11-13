import { CategoryView } from "@/components/category-view";

export default function Page() {
  return (
    <CategoryView
      title="Performance"
      categories={["performance"]}
      description="Bounded, sequential latency samples and single-page load timing from this PC. These are lab measurements, not load tests or field data; k6 load testing is not implemented."
    />
  );
}
