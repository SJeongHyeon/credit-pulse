import assert from "node:assert/strict";
import test from "node:test";

import { createFeatureLoginController } from "../backend/static/feature-login.js";

function createElement(values = {}) {
  return {
    listeners: {},
    clicked: 0,
    ...values,
    addEventListener(name, listener) {
      this.listeners[name] = listener;
    },
    getAttribute(name) {
      return this.attrs?.[name] ?? null;
    },
    click() {
      this.clicked += 1;
    },
  };
}

function createPage(hrefs) {
  const tiles = hrefs.map((href) => createElement({ attrs: { href } }));
  const elements = {
    "open-login": createElement(),
    "login-dialog": createElement(),
  };
  return {
    tiles,
    elements,
    document: {
      getElementById: (id) => elements[id],
      querySelectorAll: (selector) => {
        assert.equal(selector, "#features a[data-feature]");
        return tiles;
      },
    },
  };
}

function createStorage(overrides = {}) {
  const data = new Map();
  return {
    data,
    setItem: (key, value) => data.set(key, value),
    removeItem: (key) => data.delete(key),
    ...overrides,
  };
}

function press(tile) {
  const event = { prevented: false, preventDefault() { this.prevented = true; } };
  tile.listeners.click(event);
  return event;
}

// AC-64
test("clicking a link tile opens the login dialog instead of navigating and remembers the destination", () => {
  const page = createPage(["/reviews", "/?open=approvals", "/?open=rules"]);
  const storage = createStorage();
  createFeatureLoginController({ document: page.document, storage }).initialize();

  const event = press(page.tiles[1]);

  assert.equal(event.prevented, true);
  assert.equal(page.elements["open-login"].clicked, 1);
  assert.equal(storage.data.get("login-next"), "/?open=approvals");

  press(page.tiles[2]);
  assert.equal(storage.data.get("login-next"), "/?open=rules");
  assert.equal(page.elements["open-login"].clicked, 2);
});

test("a destination outside the allow list is not stored but the login dialog still opens", () => {
  const page = createPage(["//evil.example", "/other"]);
  const storage = createStorage();
  createFeatureLoginController({ document: page.document, storage }).initialize();

  for (const tile of page.tiles) press(tile);

  assert.equal(storage.data.size, 0);
  assert.equal(page.elements["open-login"].clicked, 2);
});

test("a storage that throws does not stop the login dialog", () => {
  const page = createPage(["/reviews"]);
  const storage = createStorage({
    setItem: () => {
      throw new Error("blocked");
    },
  });
  createFeatureLoginController({ document: page.document, storage }).initialize();

  const event = press(page.tiles[0]);

  assert.equal(event.prevented, true);
  assert.equal(page.elements["open-login"].clicked, 1);
});

test("closing the login dialog forgets the destination", () => {
  const page = createPage(["/?open=approvals"]);
  const storage = createStorage();
  createFeatureLoginController({ document: page.document, storage }).initialize();
  press(page.tiles[0]);
  assert.equal(storage.data.get("login-next"), "/?open=approvals");

  page.elements["login-dialog"].listeners.close();

  assert.equal(storage.data.has("login-next"), false);
});

test("without a storage object the controller still works", () => {
  const page = createPage(["/reviews"]);
  createFeatureLoginController({ document: page.document, storage: undefined }).initialize();

  press(page.tiles[0]);

  assert.equal(page.elements["open-login"].clicked, 1);
});
