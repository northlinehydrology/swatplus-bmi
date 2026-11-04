      module bmi_exchange_module

!!    ~ ~ ~ PURPOSE ~ ~ ~
!!    External stepping and exchange mode for coupling SWAT+ through the
!!    Basic Model Interface (BMI). The mode is off unless SWAT+ is started
!!    with the argument --exchange; with it off, none of this code changes
!!    what SWAT+ does.
!!
!!    In exchange mode SWAT+ pauses once a day, after the day's weather and
!!    decision tables and before the start-of-day water allocation, and
!!    waits for commands on standard input. Values read while paused are
!!    those of the day just completed. Values set while paused are put in
!!    place after the start-of-day water allocation and before the command
!!    (routing) loop, so SWAT+'s own daily inputs don't overwrite them.
!!    Replies go to standard output, one per line, each starting with "@".
!!    SWAT+'s own console messages go to exchange.log in the run folder.
!!    The protocol is described in docs/design.md of swatplus-bmi.
!!
!!    Only fluxes can be set. A value set while paused applies to the next
!!    day only; after that day SWAT+ goes back to its own inputs.

      use iso_fortran_env, only : input_unit

      implicit none

      private
      public :: exch_on, exch_open, exch_start, exch_pause, exch_apply, exch_end

      logical :: exch_on = .false.        !! exchange mode is on
      integer :: cmd_unit = input_unit    !! commands in
      integer :: rep_unit = 6             !! replies out
      integer :: days_done = 0            !! days completed
      integer :: days_total = 0           !! days in the simulation
      integer :: run_left = 0             !! days to run before the next pause
      logical :: run_free = .false.       !! run to the end without pausing
      integer :: protocol = 1             !! protocol version

      !! values set for the next day
      real, dimension(:), allocatable :: rec_flo         !! m3/day     |point source (recall) flow
      logical, dimension(:), allocatable :: rec_flo_on
      real, dimension(:), allocatable :: dmd_flo         !! m3/day     |place of use demand
      logical, dimension(:), allocatable :: dmd_flo_on

      integer, parameter :: nvar = 8
      character(len=48), dimension(nvar) :: var_name = [character(len=48) ::     &
            "channel_exit_water__volume_flow_rate",                             &
            "channel_water__volume",                                            &
            "aquifer_water__volume",                                            &
            "soil_water_sat-zone_top__depth",                                   &
            "aquifer_exit_water__volume_flow_rate",                             &
            "point_source_water__volume_flow_rate",                             &
            "place_of_use_water__demand_volume_flow_rate",                      &
            "place_of_use_water__withdrawal_volume_flow_rate"]
      character(len=8), dimension(nvar) :: var_grid = [character(len=8) ::      &
            "cha", "cha", "aqu", "aqu", "aqu", "rec", "pou", "pou"]
      character(len=8), dimension(nvar) :: var_units = [character(len=8) ::     &
            "m3 s-1", "m3", "m3", "m", "m3 s-1", "m3 s-1", "m3 s-1", "m3 s-1"]
      character(len=3), dimension(nvar) :: var_role = [character(len=3) ::      &
            "out", "out", "out", "out", "out", "in", "in", "out"]

      contains

!!    --------------------------------------------------------------------
      subroutine exch_open

!!    called first in main: turns exchange mode on when SWAT+ is started
!!    with --exchange, moves console messages to exchange.log and opens
!!    the reply channel on standard output

      character(len=64) :: arg = ""
      integer :: i = 0
      integer :: ios = 0
      integer :: u = 0

      do i = 1, command_argument_count()
        call get_command_argument (i, arg)
        if (trim(arg) == "--exchange") exch_on = .true.
      end do
      if (.not. exch_on) return

      !! on Mac and Linux, replies get standard output to themselves and unit 6
      !! (the console) goes to a file; elsewhere they share standard output and
      !! the "@" at the start of each reply line keeps them apart
      open (newunit=u, file="/dev/stdout", action="write", status="old", iostat=ios)
      if (ios == 0) then
        rep_unit = u
        open (6, file="exchange.log", action="write", status="replace")
      end if

      return
      end subroutine exch_open

!!    --------------------------------------------------------------------
      subroutine exch_start

!!    called in time_control before the year loop: sends the catalog

      use time_module
      use hydrograph_module, only : sp_ob, sp_ob1, ob
      use calibration_data_module, only : cal_soft, cal_hard
      use basin_module, only : prog
      use maximum_data_module, only : db_mx

      integer :: iyr = 0
      integer :: yr = 0
      integer :: ndy = 0
      integer :: d0 = 0
      integer :: d1 = 0
      integer :: iv = 0

      if (.not. exch_on) return

      if (cal_soft == "y" .or. cal_hard == "y") then
        call reply ("@error exchange mode does not run with soft or hard calibration")
        error stop 2
      end if

      !! days in the simulation, counted the way time_control counts them
      days_total = 0
      yr = time%yrc_start
      do iyr = 1, time%nbyr
        ndy = 365
        if (Mod(yr,4) == 0 .and. (Mod(yr,100) /= 0 .or. Mod(yr,400) == 0)) ndy = 366
        d0 = 1
        if (iyr == 1 .and. time%day_start > 0) d0 = time%day_start
        d1 = ndy
        if (iyr == time%nbyr .and. time%day_end > 0) d1 = min (time%day_end, ndy)
        days_total = days_total + d1 - d0 + 1
        yr = yr + 1
      end do

      allocate (rec_flo(sp_ob%recall), source = 0.)
      allocate (rec_flo_on(sp_ob%recall), source = .false.)
      allocate (dmd_flo(db_mx%wallo_pou), source = 0.)
      allocate (dmd_flo_on(db_mx%wallo_pou), source = .false.)

      call reply ("@hello swatplus-exchange " // itoa(protocol))
      call reply ("@model " // trim(adjustl(prog)))
      call reply ("@start " // itoa(time%yrc_start) // " " // itoa(max(1, time%day_start)))
      call reply ("@days " // itoa(days_total))
      call send_grid ("cha", sp_ob1%chandeg, sp_ob%chandeg)
      call send_grid ("aqu", sp_ob1%aqu, sp_ob%aqu)
      call send_grid ("rec", sp_ob1%recall, sp_ob%recall)
      call send_pou_grid
      do iv = 1, nvar
        call reply ("@var " // trim(var_name(iv)) // " " // trim(var_grid(iv)) // " " //   &
                    trim(var_role(iv)) // " " // trim(var_units(iv)))
      end do
      call reply ("@ready")

      return
      end subroutine exch_start

!!    --------------------------------------------------------------------
      subroutine exch_pause

!!    called in time_control each day before wallo_start_day: reports the
!!    days done and waits for commands

      use time_module

      if (.not. exch_on) return

      if (.not. run_free .and. run_left == 0) then
        call reply ("@paused " // itoa(days_done) // " " // itoa(time%yrc) // " " // itoa(time%day))
        call serve (.false.)
      end if

      if (.not. run_free) run_left = run_left - 1
      days_done = days_done + 1

      return
      end subroutine exch_pause

!!    --------------------------------------------------------------------
      subroutine exch_apply

!!    called in time_control each day after wallo_start_day and before
!!    call command: puts values set while paused in place for the day,
!!    then forgets them

      use time_module
      use hydrograph_module, only : sp_ob1, ob, recall

      integer :: i = 0
      integer :: irec = 0

      if (.not. exch_on) return

      do i = 1, size(rec_flo)
        if (.not. rec_flo_on(i)) cycle
        irec = ob(sp_ob1%recall + i - 1)%num
        recall(irec)%hd(time%day,time%yrs)%flo = rec_flo(i)
        rec_flo_on(i) = .false.
      end do

      do i = 1, size(dmd_flo)
        if (.not. dmd_flo_on(i)) cycle
        call set_demand (i, dmd_flo(i))
        dmd_flo_on(i) = .false.
      end do

      return
      end subroutine exch_apply

!!    --------------------------------------------------------------------
      subroutine exch_end

!!    called in time_control after the year loop: reports the end and
!!    waits until told to finish

      if (.not. exch_on) return

      call reply ("@ended " // itoa(days_done))
      if (.not. run_free) call serve (.true.)

      return
      end subroutine exch_end

!!    --------------------------------------------------------------------
      subroutine serve (at_end)

!!    reads and answers commands until one lets the simulation go on

      logical, intent(in) :: at_end
      character(len=4096) :: line = ""
      character(len=64) :: word = ""
      character(len=64) :: name = ""
      integer :: ios = 0
      integer :: n = 0
      integer :: i = 0
      real :: v = 0.

      do
        read (cmd_unit, '(a)', iostat=ios) line
        if (ios /= 0) then
          !! the caller is gone: stop where we are
          error stop 3
        end if
        word = ""
        read (line, *, iostat=ios) word

        select case (trim(word))

        case ("run")
          n = 1
          read (line, *, iostat=ios) word, n
          if (ios /= 0) n = 1
          if (at_end) then
            call reply ("@error the simulation has ended; send finish")
          else if (n < 1) then
            call reply ("@error run needs a day count of 1 or more")
          else
            run_left = n
            return
          end if

        case ("finish")
          !! run to the end without pausing and let SWAT+ complete
          run_free = .true.
          return

        case ("get")
          read (line, *, iostat=ios) word, name
          if (ios /= 0) then
            call reply ("@error get needs a variable name")
          else
            call send_values (trim(name))
          end if

        case ("set")
          read (line, *, iostat=ios) word, name, i, v
          if (ios /= 0) then
            call reply ("@error set needs a variable name, a 1-based index and a value")
          else if (at_end) then
            call reply ("@error the simulation has ended; nothing left to set")
          else
            call set_value (trim(name), i, v)
          end if

        case ("ping")
          call reply ("@ok")

        case ("stop")
          !! stop now, without completing the run
          call reply ("@bye")
          stop

        case default
          call reply ("@error unknown command: " // trim(word))

        end select
      end do

      end subroutine serve

!!    --------------------------------------------------------------------
      subroutine send_values (name)

      use hydrograph_module, only : sp_ob, sp_ob1, ob, ch_out_d, ch_stor
      use aquifer_module, only : aqu_d
      use water_allocation_module, only : poud_met
      use maximum_data_module, only : db_mx

      character(len=*), intent(in) :: name
      real, dimension(:), allocatable :: val
      integer :: i = 0
      integer :: iob = 0

      select case (name)

      case ("channel_exit_water__volume_flow_rate")
        allocate (val(sp_ob%chandeg), source = 0.)
        do i = 1, sp_ob%chandeg
          iob = sp_ob1%chandeg + i - 1
          val(i) = ch_out_d(ob(iob)%num)%flo              !m3/s
        end do

      case ("channel_water__volume")
        allocate (val(sp_ob%chandeg), source = 0.)
        do i = 1, sp_ob%chandeg
          iob = sp_ob1%chandeg + i - 1
          val(i) = ch_stor(ob(iob)%num)%flo               !m3
        end do

      case ("aquifer_water__volume")
        allocate (val(sp_ob%aqu), source = 0.)
        do i = 1, sp_ob%aqu
          iob = sp_ob1%aqu + i - 1
          val(i) = 10. * aqu_d(ob(iob)%num)%stor * ob(iob)%area_ha     !mm -> m3
        end do

      case ("soil_water_sat-zone_top__depth")
        allocate (val(sp_ob%aqu), source = 0.)
        do i = 1, sp_ob%aqu
          iob = sp_ob1%aqu + i - 1
          val(i) = aqu_d(ob(iob)%num)%dep_wt               !m
        end do

      case ("aquifer_exit_water__volume_flow_rate")
        allocate (val(sp_ob%aqu), source = 0.)
        do i = 1, sp_ob%aqu
          iob = sp_ob1%aqu + i - 1
          val(i) = ob(iob)%hd(1)%flo / 86400.              !m3 -> m3/s
        end do

      case ("point_source_water__volume_flow_rate")
        allocate (val(sp_ob%recall), source = 0.)
        do i = 1, sp_ob%recall
          iob = sp_ob1%recall + i - 1
          val(i) = ob(iob)%hd(1)%flo / 86400.              !m3 -> m3/s
        end do

      case ("place_of_use_water__demand_volume_flow_rate")
        allocate (val(db_mx%wallo_pou), source = 0.)
        do i = 1, db_mx%wallo_pou
          val(i) = poud_met(i)%duty_tot%duty / 86400.      !m3 -> m3/s
        end do

      case ("place_of_use_water__withdrawal_volume_flow_rate")
        allocate (val(db_mx%wallo_pou), source = 0.)
        do i = 1, db_mx%wallo_pou
          val(i) = poud_met(i)%duty_tot%deliv / 86400.     !m3 -> m3/s
        end do

      case default
        call reply ("@error unknown variable: " // name)
        return
      end select

      call send_array (val)

      return
      end subroutine send_values

!!    --------------------------------------------------------------------
      subroutine set_value (name, i, v)

      use hydrograph_module, only : sp_ob
      use maximum_data_module, only : db_mx

      character(len=*), intent(in) :: name
      integer, intent(in) :: i
      real, intent(in) :: v

      select case (name)

      case ("point_source_water__volume_flow_rate")
        if (i < 1 .or. i > sp_ob%recall) then
          call reply ("@error index out of range")
          return
        end if
        rec_flo(i) = v * 86400.                           !m3/s -> m3/day
        rec_flo_on(i) = .true.

      case ("place_of_use_water__demand_volume_flow_rate")
        if (i < 1 .or. i > db_mx%wallo_pou) then
          call reply ("@error index out of range")
          return
        end if
        if (v < 0.) then
          call reply ("@error a demand can't be negative")
          return
        end if
        dmd_flo(i) = v * 86400.                           !m3/s -> m3/day
        dmd_flo_on(i) = .true.

      case default
        call reply ("@error not a settable variable: " // name)
        return
      end select

      call reply ("@ok")

      return
      end subroutine set_value

!!    --------------------------------------------------------------------
      subroutine set_demand (ipou, dmd)

!!    replaces the demand wallo_start_day set for a place of use, and the
!!    duty of each of its points of diversion, the way wallo_start_day
!!    does: capped at the place of use's maximum rate and split by the
!!    points of diversion's fractions

      use water_allocation_module, only : pou, poud_met

      integer, intent(in) :: ipou
      real, intent(in) :: dmd                             !m3/day
      integer :: ipod = 0

      pou(ipou)%demand = Min(pou(ipou)%rate_max * 86400., dmd)
      poud_met(ipou)%duty_tot%duty = pou(ipou)%demand
      do ipod = 1, pou(ipou)%pods
        pou(ipou)%pod(ipod)%duty = pou(ipou)%pod(ipod)%frac * pou(ipou)%demand
        poud_met(ipou)%pod(ipod)%duty = pou(ipou)%pod(ipod)%duty
      end do

      return
      end subroutine set_demand

!!    --------------------------------------------------------------------
      subroutine send_grid (gname, ifirst, n)

      use hydrograph_module, only : ob

      character(len=*), intent(in) :: gname
      integer, intent(in) :: ifirst
      integer, intent(in) :: n
      character(len=200) :: line = ""
      integer :: i = 0
      integer :: iob = 0

      call reply ("@grid " // gname // " " // itoa(n))
      do i = 1, n
        iob = ifirst + i - 1
        write (line, '(a,1x,i0,1x,a,3(1x,es15.8e2))') gname, i, trim(ob(iob)%name),   &
                     ob(iob)%long, ob(iob)%lat, ob(iob)%area_ha
        call reply ("@obj " // trim(line))
      end do

      return
      end subroutine send_grid

!!    --------------------------------------------------------------------
      subroutine send_pou_grid

!!    water allocation places of use have no spatial object of their own;
!!    they go out with no location

      use water_allocation_module, only : pou
      use maximum_data_module, only : db_mx
      use, intrinsic :: ieee_arithmetic, only : ieee_value, ieee_quiet_nan

      character(len=200) :: line = ""
      integer :: i = 0
      real :: nan = 0.

      nan = ieee_value (nan, ieee_quiet_nan)
      call reply ("@grid pou " // itoa(db_mx%wallo_pou))
      do i = 1, db_mx%wallo_pou
        write (line, '(a,1x,i0,1x,a,3(1x,es15.8e2))') "pou", i, trim(pou(i)%name), nan, nan, nan
        call reply ("@obj " // trim(line))
      end do

      return
      end subroutine send_pou_grid

!!    --------------------------------------------------------------------
      subroutine send_array (val)

!!    one line: @value n v1 ... vn, nine significant digits so a 4-byte
!!    real goes out and comes back unchanged

      real, dimension(:), intent(in) :: val
      integer :: i = 0

      write (rep_unit, '(a,i0)', advance="no") "@value ", size(val)
      do i = 1, size(val)
        write (rep_unit, '(1x,es15.8e2)', advance="no") val(i)
      end do
      write (rep_unit, '(a)') ""
      flush (rep_unit)

      return
      end subroutine send_array

!!    --------------------------------------------------------------------
      subroutine reply (line)

      character(len=*), intent(in) :: line

      write (rep_unit, '(a)') trim(line)
      flush (rep_unit)

      return
      end subroutine reply

!!    --------------------------------------------------------------------
      function itoa (i) result (s)

      integer, intent(in) :: i
      character(len=:), allocatable :: s
      character(len=24) :: buf

      write (buf, '(i0)') i
      s = trim(buf)

      end function itoa

      end module bmi_exchange_module
