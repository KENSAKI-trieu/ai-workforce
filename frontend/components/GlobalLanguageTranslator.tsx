"use client";

import { useEffect } from "react";

import { translateUiText } from "@/lib/uiTranslation";
import { useLanguageStore } from "@/store/useLanguageStore";
import type { AppLocale } from "@/store/useLanguageStore";

interface TranslationRecord {
  source: string;
  rendered: string;
}

const textRecords = new WeakMap<Text, TranslationRecord>();
const attributeRecords = new WeakMap<Element, Map<string, TranslationRecord>>();
const translatedAttributes = ["aria-label", "placeholder", "title", "alt"] as const;
const ignoredContentSelector = "script, style, noscript, code, pre, textarea";

function shouldIgnoreText(node: Node): boolean {
  const parent = node instanceof Element ? node : node.parentElement;
  return Boolean(parent?.closest(`${ignoredContentSelector}, [data-no-translate]`));
}

function shouldIgnoreElement(element: Element): boolean {
  return Boolean(element.closest("[data-no-translate]"));
}

function translateTextNode(node: Text, locale: AppLocale) {
  if (shouldIgnoreText(node)) return;
  const current = node.data;
  let record = textRecords.get(node);

  if (!record) {
    record = { source: current, rendered: current };
    textRecords.set(node, record);
  } else if (current !== record.rendered) {
    record.source = current;
  }

  const translated = translateUiText(record.source, locale);
  record.rendered = translated;
  if (current !== translated) node.data = translated;
}

function translateAttribute(element: Element, attribute: string, locale: AppLocale) {
  const current = element.getAttribute(attribute);
  if (current === null) return;

  let records = attributeRecords.get(element);
  if (!records) {
    records = new Map();
    attributeRecords.set(element, records);
  }

  let record = records.get(attribute);
  if (!record) {
    record = { source: current, rendered: current };
    records.set(attribute, record);
  } else if (current !== record.rendered) {
    record.source = current;
  }

  const translated = translateUiText(record.source, locale);
  record.rendered = translated;
  if (current !== translated) element.setAttribute(attribute, translated);
}

function translateElement(element: Element, locale: AppLocale) {
  if (shouldIgnoreElement(element)) return;
  for (const attribute of translatedAttributes) {
    translateAttribute(element, attribute, locale);
  }
}

function translateTree(root: Node, locale: AppLocale) {
  if (root instanceof Text) {
    translateTextNode(root, locale);
    return;
  }
  if (!(root instanceof Element) || shouldIgnoreElement(root)) return;

  translateElement(root, locale);
  if (root.matches(ignoredContentSelector)) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
  let current = walker.nextNode();
  while (current) {
    if (current instanceof Text) translateTextNode(current, locale);
    else if (current instanceof Element) translateElement(current, locale);
    current = walker.nextNode();
  }
}

export default function GlobalLanguageTranslator() {
  const locale = useLanguageStore((state) => state.locale);
  const hydrateLocale = useLanguageStore((state) => state.hydrateLocale);

  useEffect(() => {
    hydrateLocale();
  }, [hydrateLocale]);

  useEffect(() => {
    const body = document.body;
    translateTree(body, locale);

    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        if (mutation.type === "characterData") {
          translateTextNode(mutation.target as Text, locale);
          continue;
        }
        if (mutation.type === "attributes") {
          translateAttribute(mutation.target as Element, mutation.attributeName || "", locale);
          continue;
        }
        for (const node of mutation.addedNodes) translateTree(node, locale);
      }
    });

    observer.observe(body, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
      attributeFilter: [...translatedAttributes],
    });

    return () => observer.disconnect();
  }, [locale]);

  return null;
}
