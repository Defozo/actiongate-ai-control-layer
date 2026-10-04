# Wybór kierunku

**Wybrany wariant: B, ActionGate**, z uzupełnieniami opisanymi w [PLAN.md](PLAN.md). Ocena obejmuje pełne plany [A](proposals/A/PLAN.md), [B](proposals/B/PLAN.md), [C](proposals/C/PLAN.md) i [D](proposals/D/PLAN.md), wskazane w [proposals/README.md](proposals/README.md), oraz oficjalny brief i regulamin.

B daje najlepsze łączne dopasowanie do zadania: ogranicza agenta do celu i uprawnień nadanych przez zaufaną aplikację, dopuszcza dokładnie określony skutek, rezerwuje budżet przed wykonaniem i zachowuje ograniczenia danych przez pamięć, parafrazy oraz delegację. Dzięki temu można pokazać użyteczny proces i udowodnić, że błąd AI nie pozwala na niedozwolony eksport. Przewaga nad D jest niewielka, ale dotyczy najważniejszego kryterium, jakości ochrony.

| Kryterium | Porównanie wartości propozycji |
| --- | --- |
| Guardrails, 30% | A ma kompletny zestaw kontroli bramy. B precyzyjnie wiąże cel workflow, prawa do skutku i ograniczenia całego kontekstu. C najdokładniej opisuje przepływy pośrednie. D bardzo dobrze zabezpiecza wykonanie zatwierdzonej operacji, lecz słabiej rozdziela pochodzenie i poufność oraz ograniczenia całego run. |
| Architektura i wydajność, 20% | B i D zachowują prostą integrację klientów przez gateway/MCP/API. D najlepiej dopracowuje spójność aktualizacji i cykl życia workerów. C daje silną izolację, ale wymaga przeniesienia agentów do własnego runtime i zatwierdzonych grafów. To koszt adopcji produktu, nie argument o czasie budowy. |
| Raportowanie, 20% | Wszystkie zapewniają dashboard i eksport. B dodaje wartościowe porównanie polityk bez ponawiania działań. C dobrze pokazuje pochodzenie ograniczeń, D łączy decyzję, koszt i stan wykonania. |
| Testy, 15% lub 20% | Wszystkie przewidują przypadki dozwolone i blokowane oraz prawdziwe AI. B/D mocno sprawdzają faktyczny skutek, budżet, replay i awarie; C wnosi szczegółowe testy przepływów pośrednich. Nie ma podstaw, aby uznać samą liczbę opisanych testów za przewagę. |
| Wdrażalność i skalowanie, 15% lub 10% | A/B/D można dołączać do istniejących integracji przy wymuszeniu kontrolowanej sieci. B łączy to ze wspólną księgą i grantem celu. C jest wartościowy dla organizacji gotowej wymienić środowisko wykonania; jako domyślna warstwa kontroli ma większe wymagania integracyjne. |

Wagi pochodzą z [briefu](materials/786a9bb4a858f98d.pdf.txt) i [regulaminu](materials/31a3fb1537ac1d02.pdf.txt): odpowiednio **30/20/20/15/15** oraz **30/20/20/20/10**. Różnica ostatnich dwóch wag nie zmienia jakościowego wyboru B, ponieważ zachowuje on pełny zakres testów i dobrą integrację. To ocena planów, nie zmierzonych produktów; nie przypisuję pozornie dokładnych ocen liczbowych ani wyników, których nie zweryfikowano.

Do końcowego planu przechodzą:

- **Z B:** broker konkretnej akcji, zaufany grant workflow, kontrola danych i budżetu oraz bezpieczne porównanie polityk.
- **Z C:** oddzielne etykiety poufności i pochodzenia, monotoniczny kontekst, synchronizacja odczytu z publikacją i deterministyczne udostępnianie publicznych pól. Bez obowiązkowego kompilatora grafów i migracji do FlowLock.
- **Z D:** jednorazowy grant w bazie, spójna generacja kontroli, rezerwa na skan wyjścia i potwierdzone zatrzymanie lokalnego workera.
- **Z A:** preflight całego kontekstu modelu oraz czytelny podział testów kontraktowych, lokalnych i komercyjnych.

Finalny plan doprecyzowuje wyścigi przy zmianie polityki i etykiet, niepewny wynik zewnętrznej akcji oraz ograniczenia replay bez surowej treści. Nie przejmuje deklaracji gotowości usług, cen lub benchmarków jako wykonanych sprawdzeń. Wybór wynika z wartości ochrony, użyteczności i dowodów dla jury, a nie oryginalności nazwy, rozbudowania opisu czy szacowanego czasu realizacji.
