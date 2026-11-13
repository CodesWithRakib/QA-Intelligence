import { CategoryView } from "@/components/category-view";

export default function Page() {
  return (
    <CategoryView
      title="Security"
      categories={["security"]}
      description="Dependency vulnerabilities (Trivy), static analysis (Semgrep), committed secrets, container configuration and HTTP security headers. Dynamic scanning (OWASP ZAP) and authenticated testing are not implemented."
    />
  );
}
