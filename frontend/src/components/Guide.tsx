/**
 * A first-time walkthrough of the page, collapsible.
 *
 * Written for someone who has never analysed a rental. Each section says what
 * a part of the page is for and what a good result looks like, before any
 * jargon. The chat can explain any of it in more depth against the user's
 * actual numbers.
 */

import { useState } from "react";

const SECTIONS: { title: string; body: string }[] = [
  {
    title: "The idea",
    body:
      "You are pretending to buy a rental property. You tell it the price, " +
      "how you would finance it, and what it rents for. It works out whether " +
      "the property would pay you money every month or cost you money, and " +
      "how good a use of your cash it is compared to leaving it in the bank.",
  },
  {
    title: "The sliders (left)",
    body:
      "These are the facts about the deal. Purchase price and rent are the big " +
      "two. Down payment is how much of the price you pay in cash; the rest is " +
      "borrowed. Interest rate and term describe the loan. The operating " +
      "sliders are the running costs, as a share of rent. Drag any of them and " +
      "every number on the right updates instantly.",
  },
  {
    title: "The tiles (top right)",
    body:
      "Six ways of judging the deal. Monthly cash flow is the plainest: what " +
      "is left after every bill, or how much you would lose. Green is good, " +
      "red is bad. Hover any tile for what it means, or ask the chat.",
  },
  {
    title: "Annual cash flow (right)",
    body:
      "The same story as a receipt: rent in at the top, each cost subtracted, " +
      "cash flow at the bottom. Net operating income is the property's profit " +
      "before the mortgage, and most of the tiles are built from it.",
  },
  {
    title: "What do nearby sales say? (bottom right)",
    body:
      "Give it a location and it finds houses that recently sold nearby and " +
      "uses them to estimate what this one is worth, as a range. It always " +
      "says how confident it is and shows you exactly which sales it used.",
  },
  {
    title: "Ask the deal (top left)",
    body:
      "Type questions in plain English. Try \"explain what all these numbers " +
      "mean\", \"is this a good deal\", or \"what's the weakest part\". You can " +
      "also change things by typing, like \"change the rate to 6.5%\". Every " +
      "figure in every answer comes from the calculator, never guessed.",
  },
];

export function Guide() {
  const [open, setOpen] = useState(false);

  return (
    <div className="panel guide">
      <button
        type="button"
        className="guide-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span>{open ? "▾" : "▸"}</span>
        <span>New here? How this page works</span>
      </button>
      {open && (
        <div className="guide-body">
          {SECTIONS.map((section) => (
            <div key={section.title} className="guide-section">
              <strong>{section.title}</strong>
              <p>{section.body}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
