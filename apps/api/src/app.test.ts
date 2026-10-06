import { describe, expect, it } from "vitest";
import { HealthController } from "./app.js";

describe("HealthController", () => {
  it("returns a stable readiness payload", () => {
    expect(new HealthController().health()).toEqual({
      status: "ok",
      service: "complex-account-api",
    });
  });
});
