# Deployment services

The reference stack uses PostgreSQL, OPA, separate local model workers, a signed
policy publisher, controlled connectors and the ActionGate gateway/dashboard.
The optional Groq connector is disabled by default.

Install and configure the stack using the [project README](../../../README.md)
and [configuration guide](../../../docs/configuration.md). Store deployment
credentials in your own psst vault; repository examples contain variable names
only. No organizer or author service account is needed for the local profile.
