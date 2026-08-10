import { stripProviderPrefix } from "../modelName";

/**
 * The two sides of this boundary name models differently and both are
 * right: the AI plane routes through LiteLLM and needs `provider/model`,
 * the frontend talks to the provider's own endpoint and must not send it.
 * Connecting them without translating is what broke the builder outright.
 */
describe("stripProviderPrefix", () => {
  it("drops the LiteLLM prefix the provider's own API rejects", () => {
    expect(stripProviderPrefix("deepseek/deepseek-v4-flash", "deepseek")).toBe(
      "deepseek-v4-flash"
    );
  });

  it("leaves a bare model name alone", () => {
    expect(stripProviderPrefix("deepseek-v4-flash", "deepseek")).toBe(
      "deepseek-v4-flash"
    );
  });

  it("keeps slashes that are part of the model's real name", () => {
    // An OpenRouter deployment genuinely routes to `meta-llama/llama-3`.
    // Cutting at the first slash would rewrite it into something that
    // does not exist, and the error would point at the wrong thing.
    expect(stripProviderPrefix("meta-llama/llama-3", "openrouter")).toBe(
      "meta-llama/llama-3"
    );
  });

  it("only strips the prefix belonging to this provider", () => {
    expect(stripProviderPrefix("anthropic/claude-sonnet-4-5", "deepseek")).toBe(
      "anthropic/claude-sonnet-4-5"
    );
  });

  it("matches the prefix case-insensitively", () => {
    expect(stripProviderPrefix("DeepSeek/deepseek-v4-pro", "deepseek")).toBe(
      "deepseek-v4-pro"
    );
  });

  it("passes through when either side is unknown", () => {
    expect(stripProviderPrefix(undefined, "deepseek")).toBeUndefined();
    expect(stripProviderPrefix("some/model", undefined)).toBe("some/model");
  });
});
