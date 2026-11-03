# 0015. Single currency

Status: Accepted, 2026-11-03.

## Context

Multi-currency money needs exchange rates, rounding rules and dates for each rate, and the product does not need any of them.

## Decision

Every amount is in US dollars.
There is no currency column and no exchange rate.

## Consequences

- Money arithmetic is simple, and totals need no conversion.
- Adding a second currency later is a decision that replaces this record.
