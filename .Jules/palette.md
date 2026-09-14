## 2024-09-14 - [Add loading spinners to inline async buttons]
**Learning:** When implementing async actions in inline cards (like TicketCard), disabled states alone are insufficient feedback. Users need an active loading indicator (like LoaderCircle) directly on the action button to confirm their click registered, especially since board refetches can take a second.
**Action:** Add spinners and `aria-busy` attributes to disabled action buttons.
