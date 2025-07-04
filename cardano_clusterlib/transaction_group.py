"""Group of methods for working with transactions."""

import itertools
import json
import logging
import pathlib as pl
import typing as tp
import warnings

from packaging import version

from cardano_clusterlib import clusterlib_helpers
from cardano_clusterlib import consts
from cardano_clusterlib import exceptions
from cardano_clusterlib import helpers
from cardano_clusterlib import structs
from cardano_clusterlib import txtools
from cardano_clusterlib import types as itp

LOGGER = logging.getLogger(__name__)


class TransactionGroup:
    def __init__(self, clusterlib_obj: "itp.ClusterLib") -> None:
        self._clusterlib_obj = clusterlib_obj
        self.min_fee = self._clusterlib_obj.genesis["protocolParams"]["minFeeB"]
        self._has_debug_prop: bool | None = None

    @property
    def _has_debug(self) -> bool:
        """Check if cardano-cli has a `debug transaction` group."""
        if self._has_debug_prop is not None:
            return self._has_debug_prop

        err = ""
        try:
            self._clusterlib_obj.cli(
                ["cardano-cli", "debug", "transaction"], add_default_args=False
            )
        except exceptions.CLIError as excp:
            err = str(excp)

        self._has_debug_prop = "Usage:" in err
        return self._has_debug_prop

    def calculate_tx_ttl(self) -> int:
        """Calculate ttl for a transaction."""
        return self._clusterlib_obj.g_query.get_slot_no() + self._clusterlib_obj.ttl_length

    def get_txid(self, tx_body_file: itp.FileType = "", tx_file: itp.FileType = "") -> str:
        """Return the transaction identifier.

        Args:
            tx_body_file: A path to the transaction body file (JSON TxBody - optional).
            tx_file: A path to the signed transaction file (JSON Tx - optional).

        Returns:
            str: A transaction ID.
        """
        cli_args = []

        # The `--output-text` option is available since 10.3.0.0 and is the default
        # until about 10.8.0.0. Then the JSON format became the default.
        # Adding the option for cli >= 10.7.0.0, because the option was already available.
        if self._clusterlib_obj.cli_version >= version.parse("10.7.0.0"):
            cli_args.append("--output-text")

        if tx_body_file:
            cli_args.extend(["--tx-body-file", str(tx_body_file)])
        elif tx_file:
            cli_args.extend(["--tx-file", str(tx_file)])
        else:
            msg = "Either `tx_body_file` or `tx_file` is needed."
            raise AssertionError(msg)

        return (
            self._clusterlib_obj.cli(["transaction", "txid", *cli_args])
            .stdout.rstrip()
            .decode("ascii")
        )

    def view_tx(self, tx_body_file: itp.FileType = "", tx_file: itp.FileType = "") -> str:
        """View a transaction.

        Args:
            tx_body_file: A path to the transaction body file (JSON TxBody - optional).
            tx_file: A path to the signed transaction file (JSON Tx - optional).

        Returns:
            str: A transaction.
        """
        cli_args = ["transaction", "view", "--output-json"]

        if tx_body_file:
            cli_args.extend(["--tx-body-file", str(tx_body_file)])
        elif tx_file:
            cli_args.extend(["--tx-file", str(tx_file)])
        else:
            msg = "Either `tx_body_file` or `tx_file` is needed."
            raise AssertionError(msg)

        if self._has_debug:
            cli_args = ["cardano-cli", "debug", *cli_args]

        return (
            self._clusterlib_obj.cli(cli_args, add_default_args=not self._has_debug)
            .stdout.rstrip()
            .decode("utf-8")
        )

    def view_tx_dict(self, tx_body_file: itp.FileType = "", tx_file: itp.FileType = "") -> dict:
        """Get a dict from transaction view.

        Args:
            tx_body_file: A path to the transaction body file (JSON TxBody - optional).
            tx_file: A path to the signed transaction file (JSON Tx - optional).

        Returns:
            dict: A transaction.
        """
        out: dict = json.loads(self.view_tx(tx_body_file=tx_body_file, tx_file=tx_file))
        return out

    def get_hash_script_data(
        self,
        script_data_file: itp.FileType | None = None,
        script_data_cbor_file: itp.FileType | None = None,
        script_data_value: str = "",
    ) -> str:
        """Return the hash of script data.

        Args:
            script_data_file: A path to the JSON file containing the script data (optional).
            script_data_cbor_file: A path to the CBOR file containing the script data (optional).
            script_data_value: A value (in JSON syntax) for the script data (optional).

        Returns:
            str: A hash of script data.
        """
        if script_data_file:
            cli_args = ["--script-data-file", str(script_data_file)]
        elif script_data_cbor_file:
            cli_args = ["--script-data-cbor-file", str(script_data_cbor_file)]
        elif script_data_value:
            cli_args = ["--script-data-value", str(script_data_value)]
        else:
            msg = (
                "Either `script_data_file`, `script_data_cbor_file` or `script_data_value` "
                "is needed."
            )
            raise AssertionError(msg)

        return (
            self._clusterlib_obj.cli(["transaction", "hash-script-data", *cli_args])
            .stdout.rstrip()
            .decode("ascii")
        )

    def get_tx_deposit(self, tx_files: structs.TxFiles) -> int:
        """Get deposit amount for a transaction (based on certificates used for the TX).

        Args:
            tx_files: A `structs.TxFiles` data container containing files needed
                for the transaction.

        Returns:
            int: A total deposit amount needed for the transaction.
        """
        if not tx_files.certificate_files:
            return 0

        pparams = self._clusterlib_obj.g_query.get_protocol_params()
        key_deposit = self._clusterlib_obj.g_query.get_address_deposit(pparams=pparams)
        pool_deposit = self._clusterlib_obj.g_query.get_pool_deposit(pparams=pparams)
        drep_deposit = self._clusterlib_obj.g_query.get_drep_deposit(pparams=pparams)
        action_deposit = self._clusterlib_obj.g_query.get_gov_action_deposit(pparams=pparams)

        deposit = 0

        for cert in tx_files.certificate_files:
            with open(cert, encoding="utf-8") as in_json:
                content = json.load(in_json)
            description = content.get("description", "")
            if (
                "Stake Address Registration" in description
                or "Stake address registration and" in description
            ):
                deposit += key_deposit
            elif "Stake Address Deregistration" in description:
                deposit -= key_deposit
            elif "Stake Pool Registration" in description:
                deposit += pool_deposit
            elif "DRep Key Registration" in description:
                deposit += drep_deposit
            elif "DRep Retirement" in description:
                deposit -= drep_deposit

        for prop in tx_files.proposal_files:
            with open(prop, encoding="utf-8") as in_json:
                content = json.load(in_json)
            ptype = content.get("type", "")
            if "Governance proposal" in ptype:
                deposit += action_deposit

        return deposit

    def build_raw_tx_bare(  # noqa: C901
        self,
        out_file: itp.FileType,
        txouts: list[structs.TxOut],
        tx_files: structs.TxFiles,
        fee: int,
        txins: structs.OptionalUTXOData = (),
        readonly_reference_txins: structs.OptionalUTXOData = (),
        script_txins: structs.OptionalScriptTxIn = (),
        return_collateral_txouts: structs.OptionalTxOuts = (),
        total_collateral_amount: int | None = None,
        mint: structs.OptionalMint = (),
        complex_certs: structs.OptionalScriptCerts = (),
        complex_proposals: structs.OptionalScriptProposals = (),
        required_signers: itp.OptionalFiles = (),
        required_signer_hashes: tp.Optional[list[str]] = None,
        ttl: int | None = None,
        withdrawals: structs.OptionalTxOuts = (),
        script_withdrawals: structs.OptionalScriptWithdrawals = (),
        script_votes: structs.OptionalScriptVotes = (),
        invalid_hereafter: int | None = None,
        invalid_before: int | None = None,
        current_treasury_value: int | None = None,
        treasury_donation: int | None = None,
        script_valid: bool = True,
        join_txouts: bool = True,
    ) -> structs.TxRawOutput:
        """Build a raw transaction.

        Args:
            out_file: An output file.
            txouts: A list (iterable) of `TxOuts`, specifying transaction outputs.
            tx_files: A `structs.TxFiles` data container containing files needed
                for the transaction.
            fee: A fee amount.
            txins: An iterable of `structs.UTXOData`, specifying input UTxOs (optional).
            readonly_reference_txins: An iterable of `structs.UTXOData`, specifying input
                UTxOs to be referenced and used as readonly (optional).
            script_txins: An iterable of `ScriptTxIn`, specifying input script UTxOs (optional).
            return_collateral_txouts: A list (iterable) of `TxOuts`, specifying transaction outputs
                for excess collateral (optional).
            total_collateral_amount: An integer indicating the total amount of collateral
                (optional).
            mint: An iterable of `Mint`, specifying script minting data (optional).
            complex_certs: An iterable of `ComplexCert`, specifying certificates script data
                (optional).
            complex_proposals: An iterable of `ComplexProposal`, specifying proposal script data
                (optional).
            required_signers: An iterable of filepaths of the signing keys whose signatures
                are required (optional).
            required_signer_hashes: A list of hashes of the signing keys whose signatures
                are required (optional).
            ttl: A last block when the transaction is still valid
                (deprecated in favor of `invalid_hereafter`, optional).
            withdrawals: A list (iterable) of `TxOuts`, specifying reward withdrawals (optional).
            script_withdrawals: An iterable of `ScriptWithdrawal`, specifying withdrawal script
                data (optional).
            script_votes: An iterable of `ScriptVote`, specifying vote script data (optional).
            invalid_hereafter: A last block when the transaction is still valid (optional).
            invalid_before: A first block when the transaction is valid (optional).
            current_treasury_value: The current treasury value (optional).
            treasury_donation: A donation to the treasury to perform (optional).
            script_valid: A bool indicating that the script is valid (True by default).
            join_txouts: A bool indicating whether to aggregate transaction outputs
                by payment address (True by default).

        Returns:
            structs.TxRawOutput: A data container with transaction output details.
        """
        if (treasury_donation is not None) != (current_treasury_value is not None):
            msg = (
                "Both `treasury_donation` and `current_treasury_value` must be specified together."
            )
            raise AssertionError(msg)

        if tx_files.certificate_files and complex_certs:
            LOGGER.warning(
                "Mixing `tx_files.certificate_files` and `complex_certs`, "
                "certs may come in unexpected order."
            )

        if tx_files.proposal_files and complex_proposals:
            LOGGER.warning(
                "Mixing `tx_files.proposal_files` and `complex_proposals`, "
                "proposals may come in unexpected order."
            )

        out_file = pl.Path(out_file)

        withdrawals, script_withdrawals, __ = txtools._get_withdrawals(
            clusterlib_obj=self._clusterlib_obj,
            withdrawals=withdrawals,
            script_withdrawals=script_withdrawals,
        )

        required_signer_hashes = required_signer_hashes or []

        txout_args, processed_txouts, txouts_count = txtools._process_txouts(
            txouts=txouts, join_txouts=join_txouts
        )

        txin_strings = txtools._get_txin_strings(txins=txins, script_txins=script_txins)

        withdrawal_strings = {f"{x.address}+{x.amount}" for x in withdrawals}

        mint_txouts = list(itertools.chain.from_iterable(m.txouts for m in mint))

        misc_args = []

        if invalid_before is not None:
            misc_args.extend(["--invalid-before", str(invalid_before)])
        if invalid_hereafter is not None:
            misc_args.extend(["--invalid-hereafter", str(invalid_hereafter)])
        elif ttl is not None:
            # `--ttl` and `--invalid-hereafter` are the same
            misc_args.extend(["--ttl", str(ttl)])

        if current_treasury_value is not None:
            misc_args.extend(["--current-treasury-value", str(current_treasury_value)])
        if treasury_donation is not None:
            misc_args.extend(["--treasury-donation", str(treasury_donation)])

        if not script_valid:
            misc_args.append("--script-invalid")

        # Only single `--mint` argument is allowed, let's aggregate all the outputs
        mint_records = [f"{m.amount} {m.coin}" for m in mint_txouts]
        misc_args.extend(["--mint", "+".join(mint_records)] if mint_records else [])

        for txin in readonly_reference_txins:
            misc_args.extend(["--read-only-tx-in-reference", f"{txin.utxo_hash}#{txin.utxo_ix}"])

        grouped_args = txtools._get_script_args(
            script_txins=script_txins,
            mint=mint,
            complex_certs=complex_certs,
            complex_proposals=complex_proposals,
            script_withdrawals=script_withdrawals,
            script_votes=script_votes,
            with_execution_units=True,
        )

        grouped_args_str = " ".join(grouped_args)
        pparams_for_txins = grouped_args and (
            "-datum-" in grouped_args_str or "-redeemer-" in grouped_args_str
        )
        # TODO: see https://github.com/input-output-hk/cardano-node/issues/4058
        pparams_for_txouts = "datum-embed-" in " ".join(txout_args)
        if pparams_for_txins or pparams_for_txouts:
            self._clusterlib_obj.create_pparams_file()
            grouped_args.extend(
                [
                    "--protocol-params-file",
                    str(self._clusterlib_obj.pparams_file),
                ]
            )

        if total_collateral_amount:
            misc_args.extend(["--tx-total-collateral", str(total_collateral_amount)])

        if tx_files.metadata_json_files and tx_files.metadata_json_detailed_schema:
            misc_args.append("--json-metadata-detailed-schema")

        proposal_file_argname = txtools.get_proposal_file_argname(
            era_in_use=self._clusterlib_obj.era_in_use
        )

        cli_args = [
            "transaction",
            "build-raw",
            "--fee",
            str(fee),
            "--out-file",
            str(out_file),
            *grouped_args,
            *helpers._prepend_flag("--tx-in", txin_strings),
            *txout_args,
            *helpers._prepend_flag("--required-signer", required_signers),
            *helpers._prepend_flag("--required-signer-hash", required_signer_hashes),
            *helpers._prepend_flag("--certificate-file", tx_files.certificate_files),
            *helpers._prepend_flag(proposal_file_argname, tx_files.proposal_files),
            *helpers._prepend_flag("--vote-file", tx_files.vote_files),
            *helpers._prepend_flag("--auxiliary-script-file", tx_files.auxiliary_script_files),
            *helpers._prepend_flag("--metadata-json-file", tx_files.metadata_json_files),
            *helpers._prepend_flag("--metadata-cbor-file", tx_files.metadata_cbor_files),
            *helpers._prepend_flag("--withdrawal", withdrawal_strings),
            *txtools._get_return_collateral_txout_args(txouts=return_collateral_txouts),
            *misc_args,
        ]

        self._clusterlib_obj.cli(cli_args)

        return structs.TxRawOutput(
            txins=list(txins),
            txouts_count=txouts_count,
            txouts=processed_txouts,
            tx_files=tx_files,
            out_file=out_file,
            fee=fee,
            build_args=cli_args,
            era=self._clusterlib_obj.era_in_use,
            script_txins=script_txins,
            script_withdrawals=script_withdrawals,
            script_votes=script_votes,
            complex_certs=complex_certs,
            complex_proposals=complex_proposals,
            mint=mint,
            invalid_hereafter=invalid_hereafter or ttl,
            invalid_before=invalid_before,
            current_treasury_value=current_treasury_value,
            treasury_donation=treasury_donation,
            withdrawals=withdrawals,
            return_collateral_txouts=return_collateral_txouts,
            total_collateral_amount=total_collateral_amount,
            readonly_reference_txins=readonly_reference_txins,
            script_valid=script_valid,
            required_signers=required_signers,
            required_signer_hashes=required_signer_hashes,
            combined_reference_txins=txtools._get_reference_txins(
                readonly_reference_txins=readonly_reference_txins,
                script_txins=script_txins,
                mint=mint,
                complex_certs=complex_certs,
                script_withdrawals=script_withdrawals,
            ),
        )

    def build_raw_tx(
        self,
        src_address: str,
        tx_name: str,
        txins: structs.OptionalUTXOData = (),
        txouts: structs.OptionalTxOuts = (),
        readonly_reference_txins: structs.OptionalUTXOData = (),
        script_txins: structs.OptionalScriptTxIn = (),
        return_collateral_txouts: structs.OptionalTxOuts = (),
        total_collateral_amount: int | None = None,
        mint: structs.OptionalMint = (),
        tx_files: structs.TxFiles | None = None,
        complex_certs: structs.OptionalScriptCerts = (),
        complex_proposals: structs.OptionalScriptProposals = (),
        fee: int = 0,
        required_signers: itp.OptionalFiles = (),
        required_signer_hashes: tp.Optional[list[str]] = None,
        ttl: int | None = None,
        withdrawals: structs.OptionalTxOuts = (),
        script_withdrawals: structs.OptionalScriptWithdrawals = (),
        script_votes: structs.OptionalScriptVotes = (),
        deposit: int | None = None,
        current_treasury_value: int | None = None,
        treasury_donation: int | None = None,
        invalid_hereafter: int | None = None,
        invalid_before: int | None = None,
        script_valid: bool = True,
        src_addr_utxos: list[structs.UTXOData] | None = None,
        join_txouts: bool = True,
        destination_dir: itp.FileType = ".",
    ) -> structs.TxRawOutput:
        """Balance inputs and outputs and build a raw transaction.

        Args:
            src_address: An address used for fee and inputs (if inputs not specified by `txins`).
            tx_name: A name of the transaction.
            txins: An iterable of `structs.UTXOData`, specifying input UTxOs (optional).
            txouts: A list (iterable) of `TxOuts`, specifying transaction outputs (optional).
            readonly_reference_txins: An iterable of `structs.UTXOData`, specifying input
                UTxOs to be referenced and used as readonly (optional).
            script_txins: An iterable of `ScriptTxIn`, specifying input script UTxOs (optional).
            return_collateral_txouts: A list (iterable) of `TxOuts`, specifying transaction outputs
                for excess collateral (optional).
            total_collateral_amount: An integer indicating the total amount of collateral
                (optional).
            mint: An iterable of `Mint`, specifying script minting data (optional).
            tx_files: A `structs.TxFiles` data container containing files needed for the transaction
                (optional).
            complex_certs: An iterable of `ComplexCert`, specifying certificates script data
                (optional).
            complex_proposals: An iterable of `ComplexProposal`, specifying proposals script data
                (optional).
            fee: A fee amount (optional).
            required_signers: An iterable of filepaths of the signing keys whose signatures
                are required (optional).
            required_signer_hashes: A list of hashes of the signing keys whose signatures
                are required (optional).
            ttl: A last block when the transaction is still valid
                (deprecated in favor of `invalid_hereafter`, optional).
            withdrawals: A list (iterable) of `TxOuts`, specifying reward withdrawals (optional).
            script_withdrawals: An iterable of `ScriptWithdrawal`, specifying withdrawal script
                data (optional).
            script_votes: An iterable of `ScriptVote`, specifying vote script data (optional).
            deposit: A deposit amount needed by the transaction (optional).
            current_treasury_value: The current treasury value (optional).
            treasury_donation: A donation to the treasury to perform (optional).
            invalid_hereafter: A last block when the transaction is still valid (optional).
            invalid_before: A first block when the transaction is valid (optional).
            script_valid: A bool indicating that the script is valid (True by default).
            src_addr_utxos: A list of UTxOs for the source address (optional).
            join_txouts: A bool indicating whether to aggregate transaction outputs
                by payment address (True by default).
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            structs.TxRawOutput: A data container with transaction output details.
        """
        destination_dir = pl.Path(destination_dir).expanduser()
        out_file = destination_dir / f"{tx_name}_tx.body"
        clusterlib_helpers._check_files_exist(out_file, clusterlib_obj=self._clusterlib_obj)

        tx_files = tx_files or structs.TxFiles()

        collected_data = txtools.collect_data_for_build(
            clusterlib_obj=self._clusterlib_obj,
            src_address=src_address,
            txins=txins,
            txouts=txouts,
            script_txins=script_txins,
            mint=mint,
            tx_files=tx_files,
            complex_certs=complex_certs,
            complex_proposals=complex_proposals,
            fee=fee,
            withdrawals=withdrawals,
            script_withdrawals=script_withdrawals,
            deposit=deposit,
            treasury_donation=treasury_donation,
            src_addr_utxos=src_addr_utxos,
            skip_asset_balancing=False,
        )

        if (
            ttl is None
            and invalid_hereafter is None
            and (self._clusterlib_obj.era_in_use == consts.Eras.SHELLEY.name.lower())
        ):
            invalid_hereafter = self.calculate_tx_ttl()

        tx_raw_output = self.build_raw_tx_bare(
            out_file=out_file,
            txouts=collected_data.txouts,
            tx_files=tx_files,
            fee=fee,
            txins=collected_data.txins,
            readonly_reference_txins=readonly_reference_txins,
            script_txins=script_txins,
            return_collateral_txouts=return_collateral_txouts,
            total_collateral_amount=total_collateral_amount,
            mint=mint,
            complex_certs=complex_certs,
            complex_proposals=complex_proposals,
            required_signers=required_signers,
            required_signer_hashes=required_signer_hashes,
            withdrawals=collected_data.withdrawals,
            script_withdrawals=collected_data.script_withdrawals,
            script_votes=script_votes,
            invalid_hereafter=invalid_hereafter or ttl,
            invalid_before=invalid_before,
            current_treasury_value=current_treasury_value,
            treasury_donation=treasury_donation,
            script_valid=script_valid,
            join_txouts=join_txouts,
        )

        helpers._check_outfiles(out_file)
        return tx_raw_output

    def estimate_fee(
        self,
        txbody_file: itp.FileType,
        txin_count: int,
        txout_count: int,
        witness_count: int,
        byron_witness_count: int = 0,
        reference_script_size: int = 0,
    ) -> int:
        """Estimate the minimum fee for a transaction.

        Args:
            txbody_file: A path to file with transaction body.
            txin_count: A number of transaction inputs.
            txout_count: A number of transaction outputs.
            witness_count: A number of witnesses.
            byron_witness_count: A number of Byron witnesses (optional).
            reference_script_size: A size in bytes of transaction reference scripts (optional).

        Returns:
            int: An estimated fee.
        """
        cli_args = []
        if self._clusterlib_obj.cli_version >= version.parse("8.22.0.0"):
            cli_args = ["--reference-script-size", str(reference_script_size)]

        self._clusterlib_obj.create_pparams_file()
        stdout = self._clusterlib_obj.cli(
            [
                "transaction",
                "calculate-min-fee",
                "--output-text",
                *self._clusterlib_obj.magic_args,
                "--protocol-params-file",
                str(self._clusterlib_obj.pparams_file),
                "--tx-in-count",
                str(txin_count),
                "--tx-out-count",
                str(txout_count),
                "--byron-witness-count",
                str(byron_witness_count),
                "--witness-count",
                str(witness_count),
                "--tx-body-file",
                str(txbody_file),
                *cli_args,
            ]
        ).stdout
        fee, *__ = stdout.decode().split()
        return int(fee)

    def calculate_tx_fee(
        self,
        src_address: str,
        tx_name: str,
        dst_addresses: tp.Optional[list[str]] = None,
        txins: structs.OptionalUTXOData = (),
        txouts: structs.OptionalTxOuts = (),
        readonly_reference_txins: structs.OptionalUTXOData = (),
        script_txins: structs.OptionalScriptTxIn = (),
        return_collateral_txouts: structs.OptionalTxOuts = (),
        total_collateral_amount: int | None = None,
        mint: structs.OptionalMint = (),
        tx_files: structs.TxFiles | None = None,
        complex_certs: structs.OptionalScriptCerts = (),
        complex_proposals: structs.OptionalScriptProposals = (),
        required_signers: itp.OptionalFiles = (),
        required_signer_hashes: tp.Optional[list[str]] = None,
        ttl: int | None = None,
        withdrawals: structs.OptionalTxOuts = (),
        script_withdrawals: structs.OptionalScriptWithdrawals = (),
        script_votes: structs.OptionalScriptVotes = (),
        deposit: int | None = None,
        current_treasury_value: int | None = None,
        treasury_donation: int | None = None,
        invalid_hereafter: int | None = None,
        invalid_before: int | None = None,
        src_addr_utxos: list[structs.UTXOData] | None = None,
        witness_count_add: int = 0,
        join_txouts: bool = True,
        destination_dir: itp.FileType = ".",
    ) -> int:
        """Build "dummy" transaction and calculate (estimate) its fee.

        Args:
            src_address: An address used for fee and inputs (if inputs not specified by `txins`).
            tx_name: A name of the transaction.
            dst_addresses: A list of destination addresses (optional)
            txins: An iterable of `structs.UTXOData`, specifying input UTxOs (optional).
            txouts: A list (iterable) of `TxOuts`, specifying transaction outputs (optional).
            readonly_reference_txins: An iterable of `structs.UTXOData`, specifying input
                UTxOs to be referenced and used as readonly (optional).
            script_txins: An iterable of `ScriptTxIn`, specifying input script UTxOs (optional).
            return_collateral_txouts: A list (iterable) of `TxOuts`, specifying transaction outputs
                for excess collateral (optional).
            total_collateral_amount: An integer indicating the total amount of collateral
                (optional).
            mint: An iterable of `Mint`, specifying script minting data (optional).
            tx_files: A `structs.TxFiles` data container containing files needed for the transaction
                (optional).
            complex_certs: An iterable of `ComplexCert`, specifying certificates script data
                (optional).
            complex_proposals: An iterable of `ComplexProposal`, specifying proposal script data
                (optional).
            required_signers: An iterable of filepaths of the signing keys whose signatures
                are required (optional).
            required_signer_hashes: A list of hashes of the signing keys whose signatures
                are required (optional).
            ttl: A last block when the transaction is still valid
                (deprecated in favor of `invalid_hereafter`, optional).
            withdrawals: A list (iterable) of `TxOuts`, specifying reward withdrawals (optional).
            script_withdrawals: An iterable of `ScriptWithdrawal`, specifying withdrawal script
                data (optional).
            script_votes: An iterable of `ScriptVote`, specifying vote script data (optional).
            deposit: A deposit amount needed by the transaction (optional).
            current_treasury_value: The current treasury value (optional).
            treasury_donation: A donation to the treasury to perform (optional).
            invalid_hereafter: A last block when the transaction is still valid (optional).
            invalid_before: A first block when the transaction is valid (optional).
            src_addr_utxos: A list of UTxOs for the source address (optional).
            witness_count_add: A number of witnesses to add - workaround to make the fee
                calculation more precise.
            join_txouts: A bool indicating whether to aggregate transaction outputs
                by payment address (True by default).
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            int: An estimated fee.
        """
        tx_files = tx_files or structs.TxFiles()
        tx_name = f"{tx_name}_estimate"

        if dst_addresses and txouts:
            LOGGER.warning(
                "The value of `dst_addresses` is ignored when value for `txouts` is available."
            )

        txouts_filled = txouts or [
            structs.TxOut(address=r, amount=1) for r in (dst_addresses or ())
        ]

        tx_raw_output = self.build_raw_tx(
            src_address=src_address,
            tx_name=tx_name,
            txins=txins,
            txouts=txouts_filled,
            readonly_reference_txins=readonly_reference_txins,
            script_txins=script_txins,
            return_collateral_txouts=return_collateral_txouts,
            total_collateral_amount=total_collateral_amount,
            mint=mint,
            tx_files=tx_files,
            complex_certs=complex_certs,
            complex_proposals=complex_proposals,
            required_signers=required_signers,
            required_signer_hashes=required_signer_hashes,
            fee=self.min_fee,
            withdrawals=withdrawals,
            script_withdrawals=script_withdrawals,
            script_votes=script_votes,
            invalid_hereafter=invalid_hereafter or ttl,
            invalid_before=invalid_before,
            src_addr_utxos=src_addr_utxos,
            deposit=deposit,
            current_treasury_value=current_treasury_value,
            treasury_donation=treasury_donation,
            join_txouts=join_txouts,
            destination_dir=destination_dir,
        )

        fee = self.estimate_fee(
            txbody_file=tx_raw_output.out_file,
            # +1 as possibly one more input will be needed for the fee amount
            txin_count=len(tx_raw_output.txins) + 1,
            txout_count=len(tx_raw_output.txouts),
            witness_count=len(tx_files.signing_key_files) + witness_count_add,
        )

        return fee

    def calculate_min_req_utxo(
        self,
        txouts: list[structs.TxOut],
    ) -> structs.Value:
        """Calculate the minimum required UTxO for a single transaction output.

        Args:
            txouts: A list of `TxOut` records that correspond to a single transaction output (UTxO).

        Returns:
            structs.Value: A data container describing the value.
        """
        if not txouts:
            msg = "No txout was specified."
            raise AssertionError(msg)

        txout_args, __, txouts_count = txtools._join_txouts(txouts=txouts)

        if txouts_count > 1:
            msg = (
                "Accepts `TxOuts` only for a single transaction txout "
                "(same address, datum, script)."
            )
            raise AssertionError(msg)

        self._clusterlib_obj.create_pparams_file()
        stdout = self._clusterlib_obj.cli(
            [
                "transaction",
                "calculate-min-required-utxo",
                "--protocol-params-file",
                str(self._clusterlib_obj.pparams_file),
                *txout_args,
            ]
        ).stdout
        coin, value = stdout.decode().split()
        return structs.Value(value=int(value), coin=coin)

    def build_tx(  # noqa: C901
        self,
        src_address: str,
        tx_name: str,
        txins: structs.OptionalUTXOData = (),
        txouts: structs.OptionalTxOuts = (),
        readonly_reference_txins: structs.OptionalUTXOData = (),
        script_txins: structs.OptionalScriptTxIn = (),
        return_collateral_txouts: structs.OptionalTxOuts = (),
        total_collateral_amount: int | None = None,
        mint: structs.OptionalMint = (),
        tx_files: structs.TxFiles | None = None,
        complex_certs: structs.OptionalScriptCerts = (),
        complex_proposals: structs.OptionalScriptProposals = (),
        change_address: str = "",
        fee_buffer: int | None = None,
        required_signers: itp.OptionalFiles = (),
        required_signer_hashes: tp.Optional[list[str]] = None,
        withdrawals: structs.OptionalTxOuts = (),
        script_withdrawals: structs.OptionalScriptWithdrawals = (),
        script_votes: structs.OptionalScriptVotes = (),
        deposit: int | None = None,
        treasury_donation: int | None = None,
        invalid_hereafter: int | None = None,
        invalid_before: int | None = None,
        witness_override: int | None = None,
        script_valid: bool = True,
        calc_script_cost_file: itp.FileType | None = None,
        join_txouts: bool = True,
        destination_dir: itp.FileType = ".",
        skip_asset_balancing: bool = True,
    ) -> structs.TxRawOutput:
        """Build a transaction.

        Args:
            src_address: An address used for fee and inputs (if inputs not specified by `txins`).
            tx_name: A name of the transaction.
            txins: An iterable of `structs.UTXOData`, specifying input UTxOs (optional).
            txouts: A list (iterable) of `TxOuts`, specifying transaction outputs (optional).
            readonly_reference_txins: An iterable of `structs.UTXOData`, specifying input
                UTxOs to be referenced and used as readonly (optional).
            script_txins: An iterable of `ScriptTxIn`, specifying input script UTxOs (optional).
            return_collateral_txouts: A list (iterable) of `TxOuts`, specifying transaction outputs
                for excess collateral (optional).
            total_collateral_amount: An integer indicating the total amount of collateral
                (optional).
            mint: An iterable of `Mint`, specifying script minting data (optional).
            tx_files: A `structs.TxFiles` data container containing files needed for the transaction
                (optional).
            complex_certs: An iterable of `ComplexCert`, specifying certificates script data
                (optional).
            complex_proposals: An iterable of `ComplexProposal`, specifying proposal script data
                (optional).
            change_address: A string with address where ADA in excess of the transaction fee
                will go to (`src_address` by default).
            fee_buffer: A buffer for fee amount (optional).
            required_signers: An iterable of filepaths of the signing keys whose signatures
                are required (optional).
            required_signer_hashes: A list of hashes of the signing keys whose signatures
                are required (optional).
            withdrawals: A list (iterable) of `TxOuts`, specifying reward withdrawals (optional).
            script_withdrawals: An iterable of `ScriptWithdrawal`, specifying withdrawal script
                data (optional).
            script_votes: An iterable of `ScriptVote`, specifying vote script data (optional).
            deposit: A deposit amount needed by the transaction (optional).
            treasury_donation: A donation to the treasury to perform (optional).
            invalid_hereafter: A last block when the transaction is still valid (optional).
            invalid_before: A first block when the transaction is valid (optional).
            witness_override: An integer indicating real number of witnesses. Can be used to fix
                fee calculation (optional).
            script_valid: A bool indicating that the script is valid (True by default).
            calc_script_cost_file: A path for output of the Plutus script cost information
                (optional).
            join_txouts: A bool indicating whether to aggregate transaction outputs
                by payment address (True by default).
            destination_dir: A path to directory for storing artifacts (optional).
            skip_asset_balancing: A bool indicating if assets balancing should be skipped
                (`build` command balance the assets automatically in newer versions).

        Returns:
            structs.TxRawOutput: A data container with transaction output details.
        """
        max_txout = [o for o in txouts if o.amount == -1 and o.coin in ("", consts.DEFAULT_COIN)]
        if max_txout:
            if change_address:
                msg = "Cannot use '-1' amount and change address at the same time."
                raise AssertionError(msg)
            change_address = max_txout[0].address
        else:
            change_address = change_address or src_address

        tx_files = tx_files or structs.TxFiles()
        if tx_files.certificate_files and complex_certs:
            LOGGER.warning(
                "Mixing `tx_files.certificate_files` and `complex_certs`, "
                "certs may come in unexpected order."
            )
        if tx_files.proposal_files and complex_proposals:
            LOGGER.warning(
                "Mixing `tx_files.proposal_files` and `complex_proposals`, "
                "proposals may come in unexpected order."
            )

        destination_dir = pl.Path(destination_dir).expanduser()

        out_file = destination_dir / f"{tx_name}_tx.body"
        clusterlib_helpers._check_files_exist(out_file, clusterlib_obj=self._clusterlib_obj)

        collected_data = txtools.collect_data_for_build(
            clusterlib_obj=self._clusterlib_obj,
            src_address=src_address,
            txins=txins,
            txouts=txouts,
            script_txins=script_txins,
            mint=mint,
            tx_files=tx_files,
            complex_certs=complex_certs,
            complex_proposals=complex_proposals,
            fee=fee_buffer or 0,
            withdrawals=withdrawals,
            script_withdrawals=script_withdrawals,
            deposit=deposit,
            treasury_donation=treasury_donation,
            skip_asset_balancing=skip_asset_balancing,
        )

        required_signer_hashes = required_signer_hashes or []

        txout_args, processed_txouts, txouts_count = txtools._process_txouts(
            txouts=collected_data.txouts, join_txouts=join_txouts
        )

        txin_strings = txtools._get_txin_strings(
            txins=collected_data.txins, script_txins=script_txins
        )

        withdrawal_strings = [f"{x.address}+{x.amount}" for x in collected_data.withdrawals]

        mint_txouts = list(itertools.chain.from_iterable(m.txouts for m in mint))

        misc_args = []

        if invalid_before is not None:
            misc_args.extend(["--invalid-before", str(invalid_before)])
        if invalid_hereafter is not None:
            misc_args.extend(["--invalid-hereafter", str(invalid_hereafter)])

        if treasury_donation is not None:
            misc_args.extend(["--treasury-donation", str(treasury_donation)])

        if not script_valid:
            misc_args.append("--script-invalid")

        # There's allowed just single `--mint` argument, let's aggregate all the outputs
        mint_records = [f"{m.amount} {m.coin}" for m in mint_txouts]
        misc_args.extend(["--mint", "+".join(mint_records)] if mint_records else [])

        for txin in readonly_reference_txins:
            misc_args.extend(["--read-only-tx-in-reference", f"{txin.utxo_hash}#{txin.utxo_ix}"])

        grouped_args = txtools._get_script_args(
            script_txins=script_txins,
            mint=mint,
            complex_certs=complex_certs,
            complex_proposals=complex_proposals,
            script_withdrawals=collected_data.script_withdrawals,
            script_votes=script_votes,
            with_execution_units=False,
        )

        if witness_override is not None:
            misc_args.extend(["--witness-override", str(witness_override)])

        if total_collateral_amount:
            misc_args.extend(["--tx-total-collateral", str(total_collateral_amount)])

        if calc_script_cost_file:
            misc_args.extend(["--calculate-plutus-script-cost", str(calc_script_cost_file)])
            out_file = pl.Path(calc_script_cost_file)
        else:
            misc_args.extend(["--out-file", str(out_file)])

        if tx_files.metadata_json_files and tx_files.metadata_json_detailed_schema:
            misc_args.append("--json-metadata-detailed-schema")

        proposal_file_argname = txtools.get_proposal_file_argname(
            era_in_use=self._clusterlib_obj.era_in_use
        )

        cli_args = [
            "transaction",
            "build",
            *grouped_args,
            *helpers._prepend_flag("--tx-in", txin_strings),
            *txout_args,
            *helpers._prepend_flag("--required-signer", required_signers),
            *helpers._prepend_flag("--required-signer-hash", required_signer_hashes),
            *helpers._prepend_flag("--certificate-file", tx_files.certificate_files),
            *helpers._prepend_flag(proposal_file_argname, tx_files.proposal_files),
            *helpers._prepend_flag("--vote-file", tx_files.vote_files),
            *helpers._prepend_flag("--auxiliary-script-file", tx_files.auxiliary_script_files),
            *helpers._prepend_flag("--metadata-json-file", tx_files.metadata_json_files),
            *helpers._prepend_flag("--metadata-cbor-file", tx_files.metadata_cbor_files),
            *helpers._prepend_flag("--withdrawal", withdrawal_strings),
            *txtools._get_return_collateral_txout_args(txouts=return_collateral_txouts),
            "--change-address",
            change_address,
            *misc_args,
            *self._clusterlib_obj.magic_args,
            *self._clusterlib_obj.socket_args,
        ]
        out = self._clusterlib_obj.cli(cli_args)
        stdout_dec = out.stdout.strip().decode("utf-8") if out.stdout else ""

        # Check for the presence of fee information. No fee information was provided in older
        # versions of the `build` command.
        estimated_fee = -1
        if stdout_dec.endswith("Lovelace"):
            estimated_fee = int(stdout_dec.split()[-2])
        elif "transaction fee" in stdout_dec:
            estimated_fee = int(stdout_dec.split()[-1])
        else:
            fee_str = self.view_tx_dict(tx_body_file=out_file).get("fee") or ""
            if fee_str.endswith("Lovelace"):
                estimated_fee = int(fee_str.split()[-2])

        return structs.TxRawOutput(
            txins=list(collected_data.txins),
            txouts=processed_txouts,
            txouts_count=txouts_count,
            tx_files=tx_files,
            out_file=out_file,
            fee=estimated_fee,
            build_args=cli_args,
            era=self._clusterlib_obj.era_in_use,
            script_txins=script_txins,
            script_withdrawals=collected_data.script_withdrawals,
            script_votes=script_votes,
            complex_certs=complex_certs,
            complex_proposals=complex_proposals,
            mint=mint,
            invalid_hereafter=invalid_hereafter,
            invalid_before=invalid_before,
            treasury_donation=treasury_donation,
            withdrawals=collected_data.withdrawals,
            change_address=change_address or src_address,
            return_collateral_txouts=return_collateral_txouts,
            total_collateral_amount=total_collateral_amount,
            readonly_reference_txins=readonly_reference_txins,
            script_valid=script_valid,
            required_signers=required_signers,
            required_signer_hashes=required_signer_hashes,
            combined_reference_txins=txtools._get_reference_txins(
                readonly_reference_txins=readonly_reference_txins,
                script_txins=script_txins,
                mint=mint,
                complex_certs=complex_certs,
                script_withdrawals=script_withdrawals,
            ),
        )

    def build_estimate_tx(  # noqa: C901
        self,
        src_address: str,
        tx_name: str,
        txins: structs.OptionalUTXOData = (),
        txouts: structs.OptionalTxOuts = (),
        readonly_reference_txins: structs.OptionalUTXOData = (),
        script_txins: structs.OptionalScriptTxIn = (),
        return_collateral_txouts: structs.OptionalTxOuts = (),
        total_collateral_amount: int | None = None,
        mint: structs.OptionalMint = (),
        tx_files: structs.TxFiles | None = None,
        complex_certs: structs.OptionalScriptCerts = (),
        complex_proposals: structs.OptionalScriptProposals = (),
        change_address: str = "",
        fee_buffer: int | None = None,
        required_signers: itp.OptionalFiles = (),
        required_signer_hashes: tp.Optional[list[str]] = None,
        withdrawals: structs.OptionalTxOuts = (),
        script_withdrawals: structs.OptionalScriptWithdrawals = (),
        script_votes: structs.OptionalScriptVotes = (),
        deposit: int | None = None,
        current_treasury_value: int | None = None,
        treasury_donation: int | None = None,
        invalid_hereafter: int | None = None,
        invalid_before: int | None = None,
        script_valid: bool = True,
        src_addr_utxos: list[structs.UTXOData] | None = None,
        witness_count_add: int = 0,
        byron_witness_count: int = 0,
        reference_script_size: int = 0,
        join_txouts: bool = True,
        destination_dir: itp.FileType = ".",
        skip_asset_balancing: bool = True,
    ) -> structs.TxRawOutput:
        """Build a balanced transaction without access to a live node.

        Args:
            src_address: An address used for fee and inputs (if inputs not specified by `txins`).
            tx_name: A name of the transaction.
            txins: An iterable of `structs.UTXOData`, specifying input UTxOs (optional).
            txouts: A list (iterable) of `TxOuts`, specifying transaction outputs (optional).
            readonly_reference_txins: An iterable of `structs.UTXOData`, specifying input
                UTxOs to be referenced and used as readonly (optional).
            script_txins: An iterable of `ScriptTxIn`, specifying input script UTxOs (optional).
            return_collateral_txouts: A list (iterable) of `TxOuts`, specifying transaction outputs
                for excess collateral (optional).
            total_collateral_amount: An integer indicating the total amount of collateral
                (optional).
            mint: An iterable of `Mint`, specifying script minting data (optional).
            tx_files: A `structs.TxFiles` data container containing files needed for the transaction
                (optional).
            complex_certs: An iterable of `ComplexCert`, specifying certificates script data
                (optional).
            complex_proposals: An iterable of `ComplexProposal`, specifying proposals script data
                (optional).
            change_address: A string with address where ADA in excess of the transaction fee
                will go to (`src_address` by default).
            fee_buffer: A buffer for fee amount (optional).
            required_signers: An iterable of filepaths of the signing keys whose signatures
                are required (optional).
            required_signer_hashes: A list of hashes of the signing keys whose signatures
                are required (optional).
            withdrawals: A list (iterable) of `TxOuts`, specifying reward withdrawals (optional).
            script_withdrawals: An iterable of `ScriptWithdrawal`, specifying withdrawal script
                data (optional).
            script_votes: An iterable of `ScriptVote`, specifying vote script data (optional).
            deposit: A deposit amount needed by the transaction (optional).
            current_treasury_value: The current treasury value (optional).
            treasury_donation: A donation to the treasury to perform (optional).
            invalid_hereafter: A last block when the transaction is still valid (optional).
            invalid_before: A first block when the transaction is valid (optional).
            script_valid: A bool indicating that the script is valid (True by default).
            src_addr_utxos: A list of UTxOs for the source address (optional).
            witness_count_add: A number of witnesses to add - workaround to make the fee
                calculation more precise.
            byron_witness_count: A number of Byron witnesses (optional).
            reference_script_size: A size in bytes of transaction reference scripts (optional).
            join_txouts: A bool indicating whether to aggregate transaction outputs
                by payment address (True by default).
            destination_dir: A path to directory for storing artifacts (optional).
            skip_asset_balancing: A bool indicating if assets balancing should be skipped.

        Returns:
            structs.TxRawOutput: A data container with transaction output details.
        """
        max_txout = [o for o in txouts if o.amount == -1 and o.coin in ("", consts.DEFAULT_COIN)]
        if max_txout:
            if change_address:
                msg = "Cannot use '-1' amount and change address at the same time."
                raise AssertionError(msg)
            change_address = max_txout[0].address
        else:
            change_address = change_address or src_address

        if (treasury_donation is not None) != (current_treasury_value is not None):
            msg = (
                "Both `treasury_donation` and `current_treasury_value` must be specified together."
            )
            raise AssertionError(msg)

        tx_files = tx_files or structs.TxFiles()
        if tx_files.certificate_files and complex_certs:
            LOGGER.warning(
                "Mixing `tx_files.certificate_files` and `complex_certs`, "
                "certs may come in unexpected order."
            )

        if tx_files.proposal_files and complex_proposals:
            LOGGER.warning(
                "Mixing `tx_files.proposal_files` and `complex_proposals`, "
                "proposals may come in unexpected order."
            )

        destination_dir = pl.Path(destination_dir).expanduser()

        out_file = destination_dir / f"{tx_name}_tx.body"
        clusterlib_helpers._check_files_exist(out_file, clusterlib_obj=self._clusterlib_obj)

        collected_data = txtools.collect_data_for_build(
            clusterlib_obj=self._clusterlib_obj,
            src_address=src_address,
            txins=txins,
            txouts=txouts,
            script_txins=script_txins,
            mint=mint,
            tx_files=tx_files,
            complex_certs=complex_certs,
            complex_proposals=complex_proposals,
            fee=fee_buffer or 0,
            withdrawals=withdrawals,
            script_withdrawals=script_withdrawals,
            deposit=deposit,
            treasury_donation=treasury_donation,
            src_addr_utxos=src_addr_utxos,
            skip_asset_balancing=skip_asset_balancing,
        )

        required_signer_hashes = required_signer_hashes or []

        txout_args, processed_txouts, txouts_count = txtools._process_txouts(
            txouts=collected_data.txouts, join_txouts=join_txouts
        )

        txin_strings = txtools._get_txin_strings(
            txins=collected_data.txins, script_txins=script_txins
        )

        withdrawal_strings = [f"{x.address}+{x.amount}" for x in collected_data.withdrawals]

        mint_txouts = list(itertools.chain.from_iterable(m.txouts for m in mint))

        script_txins_records = list(itertools.chain.from_iterable(r.txins for r in script_txins))
        combined_txins = [
            *collected_data.txins,
            *script_txins_records,
        ]
        total_utxo_value = txtools.calculate_utxos_balance(utxos=combined_txins)

        estimate_args = [
            "--shelley-key-witnesses",
            str(len(tx_files.signing_key_files) + witness_count_add),
            "--byron-key-witnesses",
            str(byron_witness_count),
            "--reference-script-size",
            str(reference_script_size),
            "--total-utxo-value",
            str(total_utxo_value),
        ]

        misc_args = []

        if invalid_before is not None:
            misc_args.extend(["--invalid-before", str(invalid_before)])
        if invalid_hereafter is not None:
            misc_args.extend(["--invalid-hereafter", str(invalid_hereafter)])

        if treasury_donation is not None:
            misc_args.extend(["--treasury-donation", str(treasury_donation)])

        if not script_valid:
            misc_args.append("--script-invalid")

        # There's allowed just single `--mint` argument, let's aggregate all the outputs
        mint_records = [f"{m.amount} {m.coin}" for m in mint_txouts]
        misc_args.extend(["--mint", "+".join(mint_records)] if mint_records else [])

        for txin in readonly_reference_txins:
            misc_args.extend(["--read-only-tx-in-reference", f"{txin.utxo_hash}#{txin.utxo_ix}"])

        grouped_args = txtools._get_script_args(
            script_txins=script_txins,
            mint=mint,
            complex_certs=complex_certs,
            complex_proposals=complex_proposals,
            script_withdrawals=collected_data.script_withdrawals,
            script_votes=script_votes,
            with_execution_units=True,
        )

        if total_collateral_amount:
            misc_args.extend(["--tx-total-collateral", str(total_collateral_amount)])

        if tx_files.metadata_json_files and tx_files.metadata_json_detailed_schema:
            misc_args.append("--json-metadata-detailed-schema")

        self._clusterlib_obj.create_pparams_file()

        cli_args = [
            "transaction",
            "build-estimate",
            "--out-file",
            str(out_file),
            *estimate_args,
            *grouped_args,
            *helpers._prepend_flag("--tx-in", txin_strings),
            *txout_args,
            *helpers._prepend_flag("--required-signer", required_signers),
            *helpers._prepend_flag("--required-signer-hash", required_signer_hashes),
            *helpers._prepend_flag("--certificate-file", tx_files.certificate_files),
            *helpers._prepend_flag("--proposal-file", tx_files.proposal_files),
            *helpers._prepend_flag("--vote-file", tx_files.vote_files),
            *helpers._prepend_flag("--auxiliary-script-file", tx_files.auxiliary_script_files),
            *helpers._prepend_flag("--metadata-json-file", tx_files.metadata_json_files),
            *helpers._prepend_flag("--metadata-cbor-file", tx_files.metadata_cbor_files),
            *helpers._prepend_flag("--withdrawal", withdrawal_strings),
            *txtools._get_return_collateral_txout_args(txouts=return_collateral_txouts),
            "--change-address",
            change_address,
            "--protocol-params-file",
            str(self._clusterlib_obj.pparams_file),
            *misc_args,
            *self._clusterlib_obj.socket_args,
        ]
        out = self._clusterlib_obj.cli(cli_args)
        stdout_dec = out.stdout.strip().decode("utf-8") if out.stdout else ""

        # Check for the presence of fee information. No fee information was provided in older
        # versions of the `build-estimate` command. Try to get the fee information from the
        # `transaction view` command if not available from the `build-estimate`.
        estimated_fee = -1
        fee_str = stdout_dec
        if not fee_str.endswith("Lovelace"):
            fee_str = self.view_tx_dict(tx_body_file=out_file).get("fee") or ""
        if fee_str.endswith("Lovelace"):
            estimated_fee = int(fee_str.split()[-2])

        return structs.TxRawOutput(
            txins=list(collected_data.txins),
            txouts=processed_txouts,
            txouts_count=txouts_count,
            tx_files=tx_files,
            out_file=out_file,
            fee=estimated_fee,
            build_args=cli_args,
            era=self._clusterlib_obj.era_in_use,
            script_txins=script_txins,
            script_withdrawals=collected_data.script_withdrawals,
            script_votes=script_votes,
            complex_certs=complex_certs,
            complex_proposals=complex_proposals,
            mint=mint,
            invalid_hereafter=invalid_hereafter,
            invalid_before=invalid_before,
            treasury_donation=treasury_donation,
            withdrawals=collected_data.withdrawals,
            change_address=change_address or src_address,
            return_collateral_txouts=return_collateral_txouts,
            total_collateral_amount=total_collateral_amount,
            readonly_reference_txins=readonly_reference_txins,
            script_valid=script_valid,
            required_signers=required_signers,
            required_signer_hashes=required_signer_hashes,
            combined_reference_txins=txtools._get_reference_txins(
                readonly_reference_txins=readonly_reference_txins,
                script_txins=script_txins,
                mint=mint,
                complex_certs=complex_certs,
                script_withdrawals=script_withdrawals,
            ),
        )

    def sign_tx(
        self,
        signing_key_files: itp.OptionalFiles,
        tx_name: str,
        tx_body_file: itp.FileType | None = None,
        tx_file: itp.FileType | None = None,
        destination_dir: itp.FileType = ".",
    ) -> pl.Path:
        """Sign a transaction.

        Args:
            signing_key_files: A list of paths to signing key files.
            tx_name: A name of the transaction.
            tx_body_file: A path to file with transaction body (optional).
            tx_file: A path to file with transaction (for incremental signing, optional).
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            Path: A path to signed transaction file.
        """
        destination_dir = pl.Path(destination_dir).expanduser()
        out_file = destination_dir / f"{tx_name}_tx.signed"
        clusterlib_helpers._check_files_exist(out_file, clusterlib_obj=self._clusterlib_obj)

        if tx_body_file:
            cli_args = ["--tx-body-file", str(tx_body_file)]
        elif tx_file:
            cli_args = ["--tx-file", str(tx_file)]
        else:
            msg = "Either `tx_body_file` or `tx_file` is needed."
            raise AssertionError(msg)

        self._clusterlib_obj.cli(
            [
                "transaction",
                "sign",
                *cli_args,
                *self._clusterlib_obj.magic_args,
                *helpers._prepend_flag("--signing-key-file", signing_key_files),
                "--out-file",
                str(out_file),
            ]
        )

        helpers._check_outfiles(out_file)
        return out_file

    def witness_tx(
        self,
        tx_body_file: itp.FileType,
        witness_name: str,
        signing_key_files: itp.OptionalFiles = (),
        destination_dir: itp.FileType = ".",
    ) -> pl.Path:
        """Create a transaction witness.

        Args:
            tx_body_file: A path to file with transaction body.
            witness_name: A name of the transaction witness.
            signing_key_files: A list of paths to signing key files (optional).
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            Path: A path to transaction witness file.
        """
        destination_dir = pl.Path(destination_dir).expanduser()
        out_file = destination_dir / f"{witness_name}_tx.witness"
        clusterlib_helpers._check_files_exist(out_file, clusterlib_obj=self._clusterlib_obj)

        self._clusterlib_obj.cli(
            [
                "transaction",
                "witness",
                "--tx-body-file",
                str(tx_body_file),
                "--out-file",
                str(out_file),
                *self._clusterlib_obj.magic_args,
                *helpers._prepend_flag("--signing-key-file", signing_key_files),
            ]
        )

        helpers._check_outfiles(out_file)
        return out_file

    def assemble_tx(
        self,
        tx_body_file: itp.FileType,
        witness_files: itp.OptionalFiles,
        tx_name: str,
        destination_dir: itp.FileType = ".",
    ) -> pl.Path:
        """Assemble a tx body and witness(es) to form a signed transaction.

        Args:
            tx_body_file: A path to file with transaction body.
            witness_files: A list of paths to transaction witness files.
            tx_name: A name of the transaction.
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            Path: A path to signed transaction file.
        """
        destination_dir = pl.Path(destination_dir).expanduser()
        out_file = destination_dir / f"{tx_name}_tx.witnessed"
        clusterlib_helpers._check_files_exist(out_file, clusterlib_obj=self._clusterlib_obj)

        self._clusterlib_obj.cli(
            [
                "transaction",
                "assemble",
                "--tx-body-file",
                str(tx_body_file),
                "--out-file",
                str(out_file),
                *helpers._prepend_flag("--witness-file", witness_files),
            ]
        )

        helpers._check_outfiles(out_file)
        return out_file

    def submit_tx_bare(self, tx_file: itp.FileType) -> str:
        """Submit a transaction, don't do any verification that it made it to the chain.

        Args:
            tx_file: A path to signed transaction file.

        Returns:
            str: A transaction ID.
        """
        out = self._clusterlib_obj.cli(
            [
                "transaction",
                "submit",
                *self._clusterlib_obj.magic_args,
                *self._clusterlib_obj.socket_args,
                "--tx-file",
                str(tx_file),
            ]
        )

        stdout_dec = out.stdout.strip().decode("utf-8") if out.stdout else ""
        txhash_maybe = stdout_dec.split("\n")[-1]
        txhash = (json.loads(txhash_maybe).get("txhash") or "") if "txhash" in txhash_maybe else ""
        return txhash

    def submit_tx(
        self,
        tx_file: itp.FileType,
        txins: list[structs.UTXOData],
        wait_blocks: int | None = None,
    ) -> str:
        """Submit a transaction, resubmit if the transaction didn't make it to the chain.

        Args:
            tx_file: A path to signed transaction file.
            txins: An iterable of `structs.UTXOData`, specifying input UTxOs.
            wait_blocks: A number of new blocks to wait for (default = 2).

        Returns:
            str: A transaction ID.
        """
        wait_blocks = (
            self._clusterlib_obj.confirm_blocks
            if wait_blocks is None or wait_blocks < 1
            else wait_blocks
        )
        txid = ""
        for r in range(20):
            err = None

            if r == 0:
                txid = self.submit_tx_bare(tx_file)
            else:
                txid = txid or self.get_txid(tx_file=tx_file)
                LOGGER.warning(f"Resubmitting transaction '{txid}' (from '{tx_file}').")
                try:
                    self.submit_tx_bare(tx_file)
                except exceptions.CLIError as exc:
                    # Check if resubmitting failed because an input UTxO was already spent
                    if "(BadInputsUTxO" not in str(exc):
                        raise
                    err = err or exc
                    # If here, the TX is likely still in mempool and we need to wait

            self._clusterlib_obj.wait_for_new_block(wait_blocks)

            # Check that one of the input UTxOs can no longer be queried in order to verify
            # the TX was successfully submitted to the chain (that the TX is no longer in mempool).
            # An input is spent when its combination of hash and ix is not found in the list
            # of current UTxOs.
            # TODO: check that the transaction is 1-block deep (can't be done in CLI alone)
            utxo_data = self._clusterlib_obj.g_query.get_utxo(utxo=txins[0])
            if not utxo_data:
                break
        else:
            if err is not None:
                # Submitting the TX raised an exception as if the input was already
                # spent, but it was either not the case, or the TX is still in mempool.
                msg = f"Failed to resubmit the transaction '{txid}' (from '{tx_file}')."
                raise exceptions.CLIError(msg) from err

            msg = f"Transaction '{txid}' didn't make it to the chain (from '{tx_file}')."
            raise exceptions.CLIError(msg)

        return txid

    def send_tx(
        self,
        src_address: str,
        tx_name: str,
        txins: structs.OptionalUTXOData = (),
        txouts: structs.OptionalTxOuts = (),
        readonly_reference_txins: structs.OptionalUTXOData = (),
        script_txins: structs.OptionalScriptTxIn = (),
        return_collateral_txouts: structs.OptionalTxOuts = (),
        total_collateral_amount: int | None = None,
        mint: structs.OptionalMint = (),
        tx_files: structs.TxFiles | None = None,
        complex_certs: structs.OptionalScriptCerts = (),
        complex_proposals: structs.OptionalScriptProposals = (),
        fee: int | None = None,
        required_signers: itp.OptionalFiles = (),
        required_signer_hashes: tp.Optional[list[str]] = None,
        ttl: int | None = None,
        withdrawals: structs.OptionalTxOuts = (),
        script_withdrawals: structs.OptionalScriptWithdrawals = (),
        script_votes: structs.OptionalScriptVotes = (),
        deposit: int | None = None,
        current_treasury_value: int | None = None,
        treasury_donation: int | None = None,
        invalid_hereafter: int | None = None,
        invalid_before: int | None = None,
        witness_count_add: int = 0,
        join_txouts: bool = True,
        verify_tx: bool = True,
        destination_dir: itp.FileType = ".",
    ) -> structs.TxRawOutput:
        """Build, Sign and Submit a transaction.

        Not recommended for complex transactions that involve Plutus scripts!

        This function uses `cardano-cli transaction build-raw` to build the transaction.
        For more complex transactions that involve Plutus scripts, consider using `build_tx`.
        The `build_tx` uses `cardano-cli transaction build` and handles execution units and
        collateral return automatically.

        Args:
            src_address: An address used for fee and inputs (if inputs not specified by `txins`).
            tx_name: A name of the transaction.
            txins: An iterable of `structs.UTXOData`, specifying input UTxOs (optional).
            txouts: A list (iterable) of `TxOuts`, specifying transaction outputs (optional).
            readonly_reference_txins: An iterable of `structs.UTXOData`, specifying input
                UTxOs to be referenced and used as readonly (optional).
            script_txins: An iterable of `ScriptTxIn`, specifying input script UTxOs (optional).
            return_collateral_txouts: A list (iterable) of `TxOuts`, specifying transaction outputs
                for excess collateral (optional).
            total_collateral_amount: An integer indicating the total amount of collateral
                (optional).
            mint: An iterable of `Mint`, specifying script minting data (optional).
            tx_files: A `structs.TxFiles` data container containing files needed for the transaction
                (optional).
            complex_certs: An iterable of `ComplexCert`, specifying certificates script data
                (optional).
            complex_proposals: An iterable of `ComplexProposal`, specifying proposal script data
                (optional).
            fee: A fee amount (optional).
            required_signers: An iterable of filepaths of the signing keys whose signatures
                are required (optional).
            required_signer_hashes: A list of hashes of the signing keys whose signatures
                are required (optional).
            ttl: A last block when the transaction is still valid
                (deprecated in favor of `invalid_hereafter`, optional).
            withdrawals: A list (iterable) of `TxOuts`, specifying reward withdrawals (optional).
            script_withdrawals: An iterable of `ScriptWithdrawal`, specifying withdrawal script
                data (optional).
            script_votes: An iterable of `ScriptVote`, specifying vote script data (optional).
            deposit: A deposit amount needed by the transaction (optional).
            current_treasury_value: The current treasury value (optional).
            treasury_donation: A donation to the treasury to perform (optional).
            invalid_hereafter: A last block when the transaction is still valid (optional).
            invalid_before: A first block when the transaction is valid (optional).
            witness_count_add: A number of witnesses to add - workaround to make the fee
                calculation more precise.
            join_txouts: A bool indicating whether to aggregate transaction outputs
                by payment address (True by default).
            verify_tx: A bool indicating whether to verify the transaction made it to chain
                and resubmit the transaction if not (True by default).
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            structs.TxRawOutput: A data container with transaction output details.
        """
        tx_files = tx_files or structs.TxFiles()

        # Resolve withdrawal amounts here (where -1 for total rewards amount is used) so the
        # resolved values can be passed around, and it is not needed to resolve them again
        # every time `_get_withdrawals` is called
        withdrawals, script_withdrawals, *__ = txtools._get_withdrawals(
            clusterlib_obj=self._clusterlib_obj,
            withdrawals=withdrawals,
            script_withdrawals=script_withdrawals,
        )

        # Get UTxOs for src address here so the records can be passed around, and it is
        # not necessary to get them once for fee calculation and again for the final transaction
        # building.
        src_addr_utxos = (
            self._clusterlib_obj.g_query.get_utxo(address=src_address)
            if fee is None and not txins
            else None
        )

        if fee is None:
            fee = self.calculate_tx_fee(
                src_address=src_address,
                tx_name=tx_name,
                txins=txins,
                txouts=txouts,
                readonly_reference_txins=readonly_reference_txins,
                script_txins=script_txins,
                return_collateral_txouts=return_collateral_txouts,
                total_collateral_amount=total_collateral_amount,
                mint=mint,
                tx_files=tx_files,
                complex_certs=complex_certs,
                complex_proposals=complex_proposals,
                required_signers=required_signers,
                required_signer_hashes=required_signer_hashes,
                withdrawals=withdrawals,
                script_withdrawals=script_withdrawals,
                deposit=deposit,
                current_treasury_value=current_treasury_value,
                treasury_donation=treasury_donation,
                invalid_hereafter=invalid_hereafter or ttl,
                src_addr_utxos=src_addr_utxos,
                witness_count_add=witness_count_add,
                join_txouts=join_txouts,
                destination_dir=destination_dir,
            )

            # Add 10% to the estimated fee, as the estimation is not precise enough, and there
            # might be another txin in the final tx once fee is added to the total needed amount
            fee = int(fee * 1.1)

        tx_raw_output = self.build_raw_tx(
            src_address=src_address,
            tx_name=tx_name,
            txins=txins,
            txouts=txouts,
            readonly_reference_txins=readonly_reference_txins,
            script_txins=script_txins,
            return_collateral_txouts=return_collateral_txouts,
            total_collateral_amount=total_collateral_amount,
            mint=mint,
            tx_files=tx_files,
            complex_certs=complex_certs,
            complex_proposals=complex_proposals,
            fee=fee,
            required_signers=required_signers,
            required_signer_hashes=required_signer_hashes,
            withdrawals=withdrawals,
            script_withdrawals=script_withdrawals,
            script_votes=script_votes,
            deposit=deposit,
            current_treasury_value=current_treasury_value,
            treasury_donation=treasury_donation,
            invalid_hereafter=invalid_hereafter or ttl,
            invalid_before=invalid_before,
            src_addr_utxos=src_addr_utxos,
            join_txouts=join_txouts,
            destination_dir=destination_dir,
        )
        tx_signed_file = self.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            tx_name=tx_name,
            signing_key_files=tx_files.signing_key_files,
            destination_dir=destination_dir,
        )
        if verify_tx:
            self.submit_tx(
                tx_file=tx_signed_file,
                txins=tx_raw_output.txins
                or [t.txins[0] for t in tx_raw_output.script_txins if t.txins],
            )
        else:
            self.submit_tx_bare(tx_file=tx_signed_file)

        return tx_raw_output

    def build_multisig_script(
        self,
        script_name: str,
        script_type_arg: str,
        payment_vkey_files: itp.OptionalFiles,
        required: int = 0,
        slot: int = 0,
        slot_type_arg: str = "",
        destination_dir: itp.FileType = ".",
    ) -> pl.Path:
        """Build a multi-signature script.

        Args:
            script_name: A name of the script.
            script_type_arg: A script type, see `MultiSigTypeArgs`.
            payment_vkey_files: A list of paths to payment vkey files.
            required: A number of required keys for the "atLeast" script type (optional).
            slot: A slot that sets script validity, depending on value of `slot_type_arg`
                (optional).
            slot_type_arg: A slot validity type, see `MultiSlotTypeArgs` (optional).
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            Path: A path to the script file.
        """
        destination_dir = pl.Path(destination_dir).expanduser()
        out_file = destination_dir / f"{script_name}_multisig.script"

        scripts_l: list[dict] = [
            {
                "keyHash": self._clusterlib_obj.g_address.get_payment_vkey_hash(
                    payment_vkey_file=f
                ),
                "type": "sig",
            }
            for f in payment_vkey_files
        ]
        if slot:
            scripts_l.append({"slot": slot, "type": slot_type_arg})

        script: dict = {
            "scripts": scripts_l,
            "type": script_type_arg,
        }

        if script_type_arg == consts.MultiSigTypeArgs.AT_LEAST:
            script["required"] = required

        with open(out_file, "w", encoding="utf-8") as fp_out:
            json.dump(script, fp_out, indent=4)

        return out_file

    def get_policyid(
        self,
        script_file: itp.FileType,
    ) -> str:
        """Calculate the PolicyId from the monetary policy script.

        Args:
            script_file: A path to the script file.

        Returns:
            str: A script policyId.
        """
        return (
            self._clusterlib_obj.cli(["transaction", "policyid", "--script-file", str(script_file)])
            .stdout.rstrip()
            .decode("utf-8")
        )

    def calculate_plutus_script_cost(
        self,
        src_address: str,
        tx_name: str,
        txins: structs.OptionalUTXOData = (),
        txouts: structs.OptionalTxOuts = (),
        readonly_reference_txins: structs.OptionalUTXOData = (),
        script_txins: structs.OptionalScriptTxIn = (),
        return_collateral_txouts: structs.OptionalTxOuts = (),
        total_collateral_amount: int | None = None,
        mint: structs.OptionalMint = (),
        tx_files: structs.TxFiles | None = None,
        complex_certs: structs.OptionalScriptCerts = (),
        complex_proposals: structs.OptionalScriptProposals = (),
        change_address: str = "",
        fee_buffer: int | None = None,
        required_signers: itp.OptionalFiles = (),
        required_signer_hashes: tp.Optional[list[str]] = None,
        withdrawals: structs.OptionalTxOuts = (),
        script_withdrawals: structs.OptionalScriptWithdrawals = (),
        script_votes: structs.OptionalScriptVotes = (),
        deposit: int | None = None,
        invalid_hereafter: int | None = None,
        invalid_before: int | None = None,
        witness_override: int | None = None,
        script_valid: bool = True,
        calc_script_cost_file: itp.FileType | None = None,
        join_txouts: bool = True,
        destination_dir: itp.FileType = ".",
    ) -> list[dict]:
        """Calculate cost of Plutus scripts. Accepts the same arguments as `build_tx`.

        Args:
            src_address: An address used for fee and inputs (if inputs not specified by `txins`).
            tx_name: A name of the transaction.
            txins: An iterable of `structs.UTXOData`, specifying input UTxOs (optional).
            txouts: A list (iterable) of `TxOuts`, specifying transaction outputs (optional).
            readonly_reference_txins: An iterable of `structs.UTXOData`, specifying input
                UTxOs to be referenced and used as readonly (optional).
            script_txins: An iterable of `ScriptTxIn`, specifying input script UTxOs (optional).
            return_collateral_txouts: A list (iterable) of `TxOuts`, specifying transaction outputs
                for excess collateral (optional).
            total_collateral_amount: An integer indicating the total amount of collateral
                (optional).
            mint: An iterable of `Mint`, specifying script minting data (optional).
            tx_files: A `structs.TxFiles` data container containing files needed for the transaction
                (optional).
            complex_certs: An iterable of `ComplexCert`, specifying certificates script data
                (optional).
            complex_proposals: An iterable of `ComplexProposal`, specifying proposal script data
                (optional).
            change_address: A string with address where ADA in excess of the transaction fee
                will go to (`src_address` by default).
            fee_buffer: A buffer for fee amount (optional).
            required_signers: An iterable of filepaths of the signing keys whose signatures
                are required (optional).
            required_signer_hashes: A list of hashes of the signing keys whose signatures
                are required (optional).
            withdrawals: A list (iterable) of `TxOuts`, specifying reward withdrawals (optional).
            script_withdrawals: An iterable of `ScriptWithdrawal`, specifying withdrawal script
                data (optional).
            script_votes: An iterable of `ScriptVote`, specifying vote script data (optional).
            deposit: A deposit amount needed by the transaction (optional).
            invalid_hereafter: A last block when the transaction is still valid (optional).
            invalid_before: A first block when the transaction is valid (optional).
            witness_override: An integer indicating real number of witnesses. Can be used to fix
                fee calculation (optional).
            script_valid: A bool indicating that the script is valid (True by default).
            calc_script_cost_file: A path for output of the Plutus script cost information
                (optional).
            join_txouts: A bool indicating whether to aggregate transaction outputs
                by payment address (True by default).
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            List[dict]: A Plutus scripts cost data.
        """
        # Collect all arguments that will be passed to `build_tx`
        kwargs = locals()
        kwargs.pop("self", None)
        kwargs.pop("kwargs", None)
        # This would be a duplicate if already present
        kwargs.pop("calc_script_cost_file", None)

        destination_dir = pl.Path(destination_dir).expanduser()
        out_file = destination_dir / f"{tx_name}_plutus.cost"

        self.build_tx(**kwargs, calc_script_cost_file=out_file)
        with open(out_file, encoding="utf-8") as fp_out:
            cost: list[dict] = json.load(fp_out)
        return cost

    def send_funds(
        self,
        src_address: str,
        destinations: list[structs.TxOut],
        tx_name: str,
        tx_files: structs.TxFiles | None = None,
        fee: int | None = None,
        ttl: int | None = None,
        deposit: int | None = None,
        invalid_hereafter: int | None = None,
        verify_tx: bool = True,
        destination_dir: itp.FileType = ".",
    ) -> structs.TxRawOutput:
        """Send funds - convenience function for `send_tx`.

        Args:
            src_address: An address used for fee and inputs.
            destinations: A list (iterable) of `TxOuts`, specifying transaction outputs.
            tx_name: A name of the transaction.
            tx_files: A `structs.TxFiles` data container containing files needed for the transaction
                (optional).
            fee: A fee amount (optional).
            ttl: A last block when the transaction is still valid
                (deprecated in favor of `invalid_hereafter`, optional).
            deposit: A deposit amount needed by the transaction (optional).
            invalid_hereafter: A last block when the transaction is still valid (optional).
            verify_tx: A bool indicating whether to verify the transaction made it to chain
                and resubmit the transaction if not (True by default).
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            structs.TxRawOutput: A data container with transaction output details.
        """
        warnings.warn(
            "`send_funds` is deprecated, use `send_tx` instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.send_tx(
            src_address=src_address,
            tx_name=tx_name,
            txouts=destinations,
            tx_files=tx_files,
            fee=fee,
            deposit=deposit,
            invalid_hereafter=invalid_hereafter or ttl,
            destination_dir=destination_dir,
            verify_tx=verify_tx,
        )

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}: clusterlib_obj={id(self._clusterlib_obj)}>"
