import { CategoryView } from "@/components/category-view";

export default function Page() {
  return (
    <CategoryView
      title="Accessibility & UX"
      categories={["accessibility", "product"]}
      description="Automated axe-core rules and responsive checks. Automated rules cover only part of WCAG: keyboard, screen-reader and UX reviews stay listed as manual, not-tested cases."
    />
  );
}
