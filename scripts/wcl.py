"""Tynd klient til WarcraftLogs v2-API (GraphQL).

Ét ansvar: skaffe et token og sende queries. Ingen forretningslogik her —
den ligger i fetch_data.py.

Vigtigt om host: WarcraftLogs kører separate sites med hver sin database.
Omen ligger på Classic Fresh, så vi skal bruge fresh.warcraftlogs.com —
ikke www. Bruger man www, får man tomme svar i stedet for en fejl, hvilket
er en irriterende måde at fejle på.
"""

import os
import time

import requests

# Skift kun denne, hvis guilden flytter site (retail = www, classic = classic).
HOST = "https://fresh.warcraftlogs.com"

TOKEN_URL = f"{HOST}/oauth/token"
GRAPHQL_URL = f"{HOST}/api/v2/client"


class WCLError(RuntimeError):
    """API'et svarede med noget, vi ikke kan arbejde videre med."""


class WCLClient:
    def __init__(self, client_id: str | None = None, client_secret: str | None = None):
        self.client_id = client_id or os.environ.get("WCL_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("WCL_CLIENT_SECRET", "")
        if not self.client_id or not self.client_secret:
            raise WCLError(
                "WCL_CLIENT_ID og WCL_CLIENT_SECRET mangler. "
                "Lav en .env ud fra .env.example, eller sæt dem som miljøvariabler."
            )
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def _get_token(self) -> str:
        """OAuth2 client credentials. Tokenet lever typisk 1 år, men vi
        cacher det alligevel pr. kørsel og forny'r, hvis det er tæt på udløb."""
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        response = requests.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
            timeout=30,
        )
        if response.status_code != 200:
            raise WCLError(
                f"Kunne ikke hente token ({response.status_code}): {response.text[:300]}"
            )

        payload = response.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.time() + payload.get("expires_in", 3600)
        return self._token

    def query(self, graphql: str, variables: dict | None = None) -> dict:
        """Kør en GraphQL-query og returnér 'data'-delen.

        GraphQL svarer 200 OK selv når query'en fejler — fejlene ligger i
        'errors'. Derfor tjekker vi dem eksplicit i stedet for at stole på
        HTTP-statuskoden.
        """
        response = requests.post(
            GRAPHQL_URL,
            json={"query": graphql, "variables": variables or {}},
            headers={"Authorization": f"Bearer {self._get_token()}"},
            timeout=60,
        )
        if response.status_code != 200:
            raise WCLError(
                f"GraphQL svarede {response.status_code}: {response.text[:300]}"
            )

        payload = response.json()
        if "errors" in payload:
            raise WCLError(f"GraphQL-fejl: {payload['errors']}")
        return payload["data"]

    def rate_limit(self) -> dict:
        """Hvor mange points har vi brugt i denne time? Grænsen er 3600."""
        data = self.query(
            """
            query {
              rateLimitData {
                limitPerHour
                pointsSpentThisHour
                pointsResetIn
              }
            }
            """
        )
        return data["rateLimitData"]
