import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// Vitest runs without globals, so Testing Library cannot register its own cleanup.
afterEach(() => {
  cleanup();
});

// Components render outside the Next.js app router in tests.
export const routerPush = vi.fn();
export const routeParams: { id: string } = { id: "run-1" };
export const searchParams = { value: new URLSearchParams() };

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
  useParams: () => routeParams,
  useSearchParams: () => searchParams.value,
}));
