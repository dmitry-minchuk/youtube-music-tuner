import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Sidebar } from "@/app/Sidebar";
import { ROUTES, ROUTE_LABELS } from "@/app/routes";

describe("Sidebar", () => {
  it("keeps every section accessible when the visual sidebar collapses", () => {
    document.body.innerHTML = renderToStaticMarkup(<Sidebar active="insights" />);

    const links = Array.from(document.querySelectorAll("nav[aria-label='Sections'] a"));
    expect(links).toHaveLength(ROUTES.length);

    for (const route of ROUTES) {
      const link = links.find((candidate) => candidate.getAttribute("href") === `#/${route}`);
      expect(link?.textContent?.trim()).toBe(ROUTE_LABELS[route]);
      expect(link?.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
    }

    const current = document.querySelector("nav a[aria-current='page']");
    expect(current?.getAttribute("href")).toBe("#/insights");
    expect(current?.textContent?.trim()).toBe("Insights");
  });
});
