from __future__ import unicode_literals

import re
import time
from collections import deque

from netmiko import log
from netmiko.base_connection import BaseConnection
from netmiko.scp_handler import BaseFileTransfer


class pica8Base(BaseConnection):
    """
    Implement methods for interacting with pica8 Networks devices.

    Disables `enable()` and `check_enable_mode()`
    methods.  Overrides several methods for pica8-specific compatibility.
    """
    def session_preparation(self):
        """
        Prepare the session after the connection has been established.

        Disable paging (the '--more--' prompts).
        Set the base prompt for interaction ('>').
        """
        self._test_channel_read()
        self.enter_cli_mode()
        self.set_base_prompt()
        self.disable_paging(command="set cli screen-length 0")
        #self.set_terminal_width(command='set cli screen-width 511')
        # Clear the read buffer
        time.sleep(.8 * self.global_delay_factor)
        self.clear_buffer()

    def _enter_shell(self):
        """Enter the Bourne Shell."""
        return self.send_command('start shell sh', expect_string=r"[\$#]")

    def _return_cli(self):
        """Return to the pica8 CLI."""
        return self.send_command('exit', expect_string=r"[#>]")

    def enter_cli_mode(self):
        """Check if at shell prompt admin@ and go into CLI."""
        delay_factor = self.select_delay_factor(delay_factor=0)
        count = 0
        cur_prompt = ''
        while count < 50:
            self.write_channel(self.RETURN)
            time.sleep(.4 * delay_factor)
            cur_prompt = self.read_channel()
            if re.search(r'admin@', cur_prompt) or re.search(r":~\$$", cur_prompt.strip()):
                self.write_channel("cli" + self.RETURN)
                time.sleep(.8 * delay_factor)
                self.clear_buffer()
                break
            elif '>' in cur_prompt or '#' in cur_prompt:
                break
            count += 1

    def check_enable_mode(self, *args, **kwargs):
        """No enable mode on pica8."""
        pass

    def enable(self, *args, **kwargs):
        """No enable mode on pica8."""
        pass

    def exit_enable_mode(self, *args, **kwargs):
        """No enable mode on pica8."""
        pass

    def check_config_mode(self, check_string='#'):
        """Checks if the device is in configuration mode or not."""
        return super().check_config_mode(check_string=check_string)

    def config_mode(self, config_command='configure'):
        """Enter configuration mode."""
        return super().config_mode(config_command=config_command)

    def exit_config_mode(self, exit_config='exit configuration-mode'):
        """Exit configuration mode."""
        output = ""
        if self.check_config_mode():
            output = self.send_command_timing(exit_config, strip_prompt=False, strip_command=False)
            if 'There are uncommitted changes' in output:
                output += self.send_command_timing('exit discard', strip_prompt=False,
                                                   strip_command=False)
            if self.check_config_mode():
                raise ValueError("Failed to exit configuration mode")
        return output

    def commit(self, confirm=False, confirm_delay=None, check=False, comment='',
               and_quit=False, delay_factor=1):
        """
        Commit the candidate configuration.

        Commit the entered configuration. Raise an error and return the failure
        if the commit fails.

        Automatically enters configuration mode

        default:
            command_string = commit
        check and (confirm or confirm_dely or comment):
            Exception
        confirm_delay and no confirm:
            Exception
        confirm:
            confirm_delay option
            comment option
            command_string = commit confirmed or commit confirmed <confirm_delay>
        check:
            command_string = commit check

        """
        delay_factor = self.select_delay_factor(delay_factor)

        if check and (confirm or confirm_delay or comment):
            raise ValueError("Invalid arguments supplied with commit check")

        if confirm_delay and not confirm:
            raise ValueError("Invalid arguments supplied to commit method both confirm and check")

        # Select proper command string based on arguments provided
        command_string = 'commit'
        commit_marker = 'Commit OK.'
        if check:
            command_string = 'commit check'
            commit_marker = 'Commit check OK.'
        elif confirm:
            if confirm_delay:
                command_string = 'commit confirmed ' + str(confirm_delay)
            else:
                command_string = 'commit confirmed'
            commit_marker = 'Will be automatically rolled back in'


        # Enter config mode (if necessary)
        output = self.config_mode()
        # and_quit will get out of config mode on commit
        if and_quit:
            prompt = self.base_prompt
            output += self.send_command_expect(command_string, expect_string=prompt,
                                               strip_prompt=False,
                                               strip_command=False, delay_factor=delay_factor)
        else:
            output += self.send_command_expect(command_string, strip_prompt=False,
                                               strip_command=False, delay_factor=delay_factor)

        if commit_marker not in output:
            raise ValueError(f"Commit failed with the following errors:\n\n{output}")

        return output

    def strip_command(self, command_string, output):
        """
        Strip command_string from output string

        Cisco IOS adds backspaces into output for long commands (i.e. for commands that line wrap)

        :param command_string: The command string sent to the device
        :type command_string: str

        :param output: The returned output as a result of the command string sent to the device
        :type output: str
        """
        backspace_char = "\x08"

        # Check for line wrap (remove backspaces)
        if backspace_char in output:
            output = output.replace(backspace_char, "")

        # Juniper has a weird case where the echoed command will be " \n"
        # i.e. there is an extra space there.
        # Pica8 has a wierd case where the echoed command starts with a "\n" followed by the command on the second line
        # i.e. a newline character is at the start
        cmd = command_string.strip()
        if output.startswith(cmd):
            output_lines = output.split(self.RESPONSE_RETURN)
            new_output = output_lines[1:]
            return self.RESPONSE_RETURN.join(new_output)
        elif output.startswith('\n'):
            output_lines = output.split('\n')
            new_output = output_lines[1:]
            return self.RESPONSE_RETURN.join(new_output)
            
        else:
            # command_string isn't there; do nothing
            return output

    def send_command(
        self,
        command_string,
        expect_string=None,
        delay_factor=1,
        max_loops=500,
        auto_find_prompt=True,
        strip_prompt=True,
        strip_command=True,
        normalize=True,
        use_textfsm=False,
        textfsm_template=None,
        use_genie=False,
        cmd_verify=False,
    ):
        """Execute command_string on the SSH channel using a pattern-based mechanism. Generally
        used for show commands. By default this method will keep waiting to receive data until the
        network device prompt is detected. The current network device prompt will be determined
        automatically.
        :param command_string: The command to be executed on the remote device.
        :type command_string: str
        :param expect_string: Regular expression pattern to use for determining end of output.
            If left blank will default to being based on router prompt.
        :type expect_string: str
        :param delay_factor: Multiplying factor used to adjust delays (default: 1).
        :type delay_factor: int
        :param max_loops: Controls wait time in conjunction with delay_factor. Will default to be
            based upon self.timeout.
        :type max_loops: int
        :param strip_prompt: Remove the trailing router prompt from the output (default: True).
        :type strip_prompt: bool
        :param strip_command: Remove the echo of the command from the output (default: True).
        :type strip_command: bool
        :param normalize: Ensure the proper enter is sent at end of command (default: True).
        :type normalize: bool
        :param use_textfsm: Process command output through TextFSM template (default: False).
        :type normalize: bool
        :param textfsm_template: Name of template to parse output with; can be fully qualified
            path, relative path, or name of file in current directory. (default: None).
        :param use_genie: Process command output through PyATS/Genie parser (default: False).
        :type normalize: bool
        :param cmd_verify: Verify command echo before proceeding (default: False).
        :type cmd_verify: bool
        """
        # Time to delay in each read loop
        loop_delay = 0.2

        # Default to making loop time be roughly equivalent to self.timeout (support old max_loops
        # and delay_factor arguments for backwards compatibility).
        delay_factor = self.select_delay_factor(delay_factor)
        if delay_factor == 1 and max_loops == 500:
            # Default arguments are being used; use self.timeout instead
            max_loops = int(self.timeout / loop_delay)

        # Find the current router prompt
        if expect_string is None:
            if auto_find_prompt:
                try:
                    prompt = self.find_prompt(delay_factor=delay_factor)
                except ValueError:
                    prompt = self.base_prompt
            else:
                prompt = self.base_prompt
            search_pattern = re.escape(prompt.strip())
        else:
            search_pattern = expect_string

        if normalize:
            command_string = self.normalize_cmd(command_string)

        time.sleep(delay_factor * loop_delay)
        self.clear_buffer()
        self.write_channel(command_string)
        new_data = ""

        cmd = command_string.strip()
        # if cmd is just an "enter" skip this section
        if cmd and cmd_verify:
            # Make sure you read until you detect the command echo (avoid getting out of sync)
            new_data = self.read_until_pattern(pattern=re.escape(cmd))
            new_data = self.normalize_linefeeds(new_data)
            # Strip off everything before the command echo (to avoid false positives on the prompt)
            if new_data.count(cmd) == 1:
                new_data = new_data.split(cmd)[1:]
                new_data = self.RESPONSE_RETURN.join(new_data)
                new_data = new_data.lstrip()
                new_data = f"{cmd}{self.RESPONSE_RETURN}{new_data}"

        i = 1
        output = ""
        past_three_reads = deque(maxlen=3)
        first_line_processed = False

        # Keep reading data until search_pattern is found or until max_loops is reached.
        while i <= max_loops:
            if new_data:
                output += new_data
                past_three_reads.append(new_data)

                # Case where we haven't processed the first_line yet (there is a potential issue
                # in the first line (in cases where the line is repainted).
                if not first_line_processed:
                    output, first_line_processed = self._first_line_handler(
                        output, search_pattern
                    )
                    # Check if we have already found our pattern
                    if re.search(search_pattern, output):
                        break

                else:
                    # Check if pattern is in the past three reads
                    if re.search(search_pattern, "".join(past_three_reads)):
                        break

            time.sleep(delay_factor * loop_delay)
            i += 1
            new_data = self.read_channel()
        else:  # nobreak
            raise IOError(
                "Search pattern never detected in send_command_expect: {}".format(
                    search_pattern
                )
            )

        output = self._sanitize_output(
            output,
            strip_command=strip_command,
            command_string=command_string,
            strip_prompt=strip_prompt,
        )

        # If both TextFSM and Genie are set, try TextFSM then Genie
        if use_textfsm:
            structured_output = get_structured_data(
                output,
                platform=self.device_type,
                command=command_string.strip(),
                template=textfsm_template,
            )
            # If we have structured data; return it.
            if not isinstance(structured_output, str):
                return structured_output
        if use_genie:
            structured_output = get_structured_data_genie(
                output, platform=self.device_type, command=command_string.strip()
            )
            # If we have structured data; return it.
            if not isinstance(structured_output, str):
                return structured_output
        return output

    def send_config_set(
        self,
        config_commands=None,
        exit_config_mode=True,
        delay_factor=1,
        max_loops=150,
        strip_prompt=False,
        strip_command=False,
        config_mode_command=None,
        cmd_verify=False,
        enter_config_mode=True,
    ):
        """
        Send configuration commands down the SSH channel.
        config_commands is an iterable containing all of the configuration commands.
        The commands will be executed one after the other.
        Automatically exits/enters configuration mode.
        :param config_commands: Multiple configuration commands to be sent to the device
        :type config_commands: list or string
        :param exit_config_mode: Determines whether or not to exit config mode after complete
        :type exit_config_mode: bool
        :param delay_factor: Factor to adjust delays
        :type delay_factor: int
        :param max_loops: Controls wait time in conjunction with delay_factor (default: 150)
        :type max_loops: int
        :param strip_prompt: Determines whether or not to strip the prompt
        :type strip_prompt: bool
        :param strip_command: Determines whether or not to strip the command
        :type strip_command: bool
        :param config_mode_command: The command to enter into config mode
        :type config_mode_command: str
        :param cmd_verify: Whether or not to verify command echo for each command in config_set
        :type cmd_verify: bool
        :param enter_config_mode: Do you enter config mode before sending config commands
        :type exit_config_mode: bool
        """
        delay_factor = self.select_delay_factor(delay_factor)
        if config_commands is None:
            return ""
        elif isinstance(config_commands, str):
            config_commands = (config_commands,)

        if not hasattr(config_commands, "__iter__"):
            raise ValueError("Invalid argument passed into send_config_set")

        # Send config commands
        output = ""
        if enter_config_mode:
            cfg_mode_args = (config_mode_command,) if config_mode_command else tuple()
            output += self.config_mode(*cfg_mode_args)

        if self.fast_cli:
            for cmd in config_commands:
                self.write_channel(self.normalize_cmd(cmd))
            # Gather output
            output += self._read_channel_timing(
                delay_factor=delay_factor, max_loops=max_loops
            )
        elif not cmd_verify:
            for cmd in config_commands:
                self.write_channel(self.normalize_cmd(cmd))
                time.sleep(delay_factor * 0.05)
            # Gather output
            output += self._read_channel_timing(
                delay_factor=delay_factor, max_loops=max_loops
            )
        else:
            for cmd in config_commands:
                self.write_channel(self.normalize_cmd(cmd))

                # Make sure command is echoed
                new_output = self.read_until_pattern(pattern=re.escape(cmd.strip()))
                output += new_output

                # We might capture next prompt in the original read
                pattern = f"(?:{re.escape(self.base_prompt)}|#)"
                if not re.search(pattern, new_output):
                    # Make sure trailing prompt comes back (after command)
                    # NX-OS has fast-buffering problem where it immediately echoes command
                    # Even though the device hasn't caught up with processing command.
                    new_output = self.read_until_pattern(pattern=pattern)
                    output += new_output

        if exit_config_mode:
            output += self.exit_config_mode()
        output = self._sanitize_output(output)
        log.debug(f"{output}")
        return output

    def strip_prompt(self, *args, **kwargs):
        """Strip the trailing router prompt from the output."""
        a_string = super(pica8Base, self).strip_prompt(*args, **kwargs)
        return self.strip_context_items(a_string)

    def strip_context_items(self, a_string):
        """Strip pica8-specific output.

        pica8 will also put a configuration context:
        [edit]

        and various chassis contexts:
        {master:0}, {backup:1}

        This method removes those lines.
        """
        strings_to_strip = [
            r'\[edit.*\]',
            r'\{master:.*\}',
            r'\{backup:.*\}',
            r'\{line.*\}',
            r'\{primary.*\}',
            r'\{secondary.*\}',
        ]

        response_list = a_string.split(self.RESPONSE_RETURN)
        last_line = response_list[-1]

        for pattern in strings_to_strip:
            if re.search(pattern, last_line):
                return self.RESPONSE_RETURN.join(response_list[:-1])
        return a_string


class pica8SSH(pica8Base):
    pass


class pica8Telnet(pica8Base):
    def __init__(self, *args, **kwargs):
        default_enter = kwargs.get('default_enter')
        kwargs['default_enter'] = '\r\n' if default_enter is None else default_enter
        super(pica8Telnet, self).__init__(*args, **kwargs)


class pica8FileTransfer(BaseFileTransfer):
    """pica8 SCP File Transfer driver."""
    def __init__(self, ssh_conn, source_file, dest_file, file_system="/var/tmp", direction='put'):
        return super(pica8FileTransfer, self).__init__(ssh_conn=ssh_conn,
                                                       source_file=source_file,
                                                       dest_file=dest_file,
                                                       file_system=file_system,
                                                       direction=direction)

    def remote_space_available(self, search_pattern=""):
        """Return space available on remote device."""
        return self._remote_space_available_unix(search_pattern=search_pattern)

    def check_file_exists(self, remote_cmd=""):
        """Check if the dest_file already exists on the file system (return boolean)."""
        return self._check_file_exists_unix(remote_cmd=remote_cmd)

    def remote_file_size(self, remote_cmd="", remote_file=None):
        """Get the file size of the remote file."""
        return self._remote_file_size_unix(remote_cmd=remote_cmd, remote_file=remote_file)

    def remote_md5(self, base_cmd='file checksum md5', remote_file=None):
        return super(pica8FileTransfer, self).remote_md5(base_cmd=base_cmd,
                                                         remote_file=remote_file)

    def enable_scp(self, cmd=None):
        raise NotImplementedError

    def disable_scp(self, cmd=None):
        raise NotImplementedError
