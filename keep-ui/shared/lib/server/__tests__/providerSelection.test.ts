import { selectAiProvider } from "../selectAiProvider";

/**
 * Which installed provider an AI feature ends up using.
 *
 * This selection had no test at all, which is how it shipped choosing
 * whichever provider the API happened to return first.
 */

interface Installed {
  id: string;
  type: string;
  details?: { authentication?: Record<string, string> };
}

const AI_TYPES = ["openai", "deepseek", "anthropic", "gemini", "ollama"];

const select = (
  installed: Installed[],
  routing: { provider?: string; providerId?: string }
) => selectAiProvider(installed, AI_TYPES, routing);

const withKey = (id: string, type: string): Installed => ({
  id,
  type,
  details: { authentication: { api_key: `key-${id}` } },
});

const withoutKey = (id: string, type: string): Installed => ({
  id,
  type,
  details: { authentication: {} },
});

describe("choosing the provider an AI feature uses", () => {
  it("skips a provider whose secret is missing", () => {
    // Exactly the shape found in production: a row left behind whose
    // secret file was gone. Choosing it and only then noticing lost the
    // assistant outright, with a working provider next in the list.
    const chosen = select(
      [withoutKey("orphan", "deepseek"), withKey("good", "deepseek")],
      {}
    );

    expect(chosen?.id).toBe("good");
  });

  it("returns nothing when no provider can authenticate", () => {
    expect(select([withoutKey("orphan", "deepseek")], {})).toBeUndefined();
  });

  it("uses the specific installation when one is configured", () => {
    // Two accounts of the same vendor is a real configuration — a cheap
    // key and an expensive one both appear as `deepseek`.
    const chosen = select(
      [withKey("cheap", "deepseek"), withKey("expensive", "deepseek")],
      { provider: "deepseek", providerId: "expensive" }
    );

    expect(chosen?.id).toBe("expensive");
  });

  it("falls back to the type when no specific installation is named", () => {
    const chosen = select(
      [withKey("a", "openai"), withKey("b", "deepseek")],
      { provider: "deepseek" }
    );

    expect(chosen?.id).toBe("b");
  });

  it("does not honour an id whose provider cannot authenticate", () => {
    const chosen = select(
      [withoutKey("named", "deepseek"), withKey("other", "deepseek")],
      { providerId: "named" }
    );

    expect(chosen?.id).toBe("other");
  });

  it("ignores an id that no longer exists rather than failing", () => {
    // A provider can be deleted while a function still points at it.
    const chosen = select([withKey("current", "deepseek")], {
      providerId: "deleted-last-week",
      provider: "deepseek",
    });

    expect(chosen?.id).toBe("current");
  });

  it("ignores providers that are not AI providers at all", () => {
    const chosen = select(
      [withKey("dd", "datadog"), withKey("ds", "deepseek")],
      {}
    );

    expect(chosen?.id).toBe("ds");
  });

  it("still picks something when nothing is configured", () => {
    // Unconfigured must keep working — this is the pre-existing behaviour
    // every deployment relies on.
    expect(select([withKey("only", "deepseek")], {})?.id).toBe("only");
  });
});
